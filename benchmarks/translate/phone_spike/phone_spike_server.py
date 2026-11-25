"""Phase 0, P0.5: phone audio spike server.

Runs in the project venv (Starlette + uvicorn, already BibleLive dependencies).
Serves a test page and the clips from phone_spike_clips.py, and broadcasts one
clip at a time over a WebSocket on a sermon-like schedule (clip length plus a
0.5-4 s pause, a 10 s pause every 12th clip). Clients that are disconnected miss
clips, as they would in a live service.

    python benchmarks/translate/phone_spike/phone_spike_server.py [--port 8093]
    cloudflared tunnel --url http://127.0.0.1:8093

Open the printed URL (it carries the access key) on the phone through the
tunnel's https://<name>.trycloudflare.com address. Every page event is
logged to results/translate/phone_spike/logs/<run>/events.jsonl; summarize with
phone_spike_summary.py.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import json
import random
import secrets
import time
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import uvicorn
from phone_spike_clips import OUT_DIR as CLIPS_DIR
from phone_spike_clips import RESULTS
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, Response
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

HERE = Path(__file__).resolve().parent
LOG_ROOT = RESULTS / "translate" / "phone_spike" / "logs"
MEDIA_TYPES = {"mp3": "audio/mpeg", "aac": "audio/aac", "m4a": "audio/mp4"}


def schedule_gaps(n: int, seed: int = 5) -> list[float]:
    """Pause after each clip: 0.5-4 s, and 10 s after every 12th clip."""
    rng = random.Random(seed)
    return [10.0 if (i + 1) % 12 == 0 else round(rng.uniform(0.5, 4.0), 2) for i in range(n)]


class Spike:
    def __init__(self, key: str, log_dir: Path) -> None:
        self.key = key
        manifest = json.loads((CLIPS_DIR / "manifest.json").read_text(encoding="utf-8"))
        self.clips: list[dict[str, Any]] = manifest["clips"]
        self.gaps = schedule_gaps(len(self.clips))
        # socket -> clip kind to send inline (base64 in the message), or None to fetch by URL
        self.sockets: dict[WebSocket, str | None] = {}
        self._inline: dict[tuple[str, str], str] = {}
        log_dir.mkdir(parents=True, exist_ok=True)
        self.log_file = (log_dir / "events.jsonl").open("a", encoding="utf-8")
        self.seq = 0

    def log(self, rec: dict[str, Any]) -> None:
        rec = {"t": round(time.time(), 3), **rec}
        self.log_file.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self.log_file.flush()

    def inline(self, kind: str, name: str) -> str:
        key = (kind, name)
        if key not in self._inline:
            data = (CLIPS_DIR / kind / f"{name}.{kind}").read_bytes()
            self._inline[key] = base64.b64encode(data).decode("ascii")
        return self._inline[key]

    async def broadcast_loop(self) -> None:
        i = 0
        while True:
            clip = self.clips[i % len(self.clips)]
            self.seq += 1
            msg = {"type": "clip", "seq": self.seq, "name": clip["name"],
                   "text": clip["text"], "dur": clip["duration_s"]}  # fmt: skip
            self.log({"ev": "sent", "seq": self.seq, "name": clip["name"],
                      "listeners": len(self.sockets)})  # fmt: skip
            for ws, kind in list(self.sockets.items()):
                out = {**msg, "data": self.inline(kind, clip["name"])} if kind else msg
                try:
                    await asyncio.wait_for(ws.send_json(out), timeout=2)
                except Exception:
                    self.sockets.pop(ws, None)
            await asyncio.sleep(clip["duration_s"] + self.gaps[i % len(self.gaps)])
            i += 1


def build_app(spike: Spike) -> Starlette:
    def authorized(request: Request) -> bool:
        return secrets.compare_digest(request.query_params.get("k", ""), spike.key)

    async def index(request: Request) -> Response:
        if not authorized(request):
            return Response("not found", status_code=404)
        return HTMLResponse((HERE / "index.html").read_text(encoding="utf-8"),
                            headers={"Cache-Control": "no-store"})  # fmt: skip

    async def clip(request: Request) -> Response:
        if not authorized(request):
            return Response("not found", status_code=404)
        kind, name = request.path_params["kind"], request.path_params["name"]
        if kind not in MEDIA_TYPES or not name.isdigit():
            return Response("not found", status_code=404)
        path = CLIPS_DIR / kind / f"{name}.{kind}"
        if not path.exists():
            return Response("not found", status_code=404)
        return FileResponse(path, media_type=MEDIA_TYPES[kind])

    async def health(_request: Request) -> Response:
        return JSONResponse({"listeners": len(spike.sockets), "seq": spike.seq})

    async def ws_endpoint(ws: WebSocket) -> None:
        await ws.accept()
        try:
            hello = await asyncio.wait_for(ws.receive_json(), timeout=5)
        except (TimeoutError, WebSocketDisconnect, ValueError):
            await ws.close(code=4401)
            return
        if not isinstance(hello, dict) or not secrets.compare_digest(
            str(hello.get("k", "")), spike.key
        ):
            await ws.close(code=4401)
            return
        client = str(hello.get("client", "?"))[:80]
        spike.log({"ev": "connect", "client": client, "setup": hello.get("setup"),
                   "ua": ws.headers.get("user-agent", "")[:200]})  # fmt: skip
        setup = hello.get("setup")
        kind = setup.get("inline") if isinstance(setup, dict) else None
        spike.sockets[ws] = kind if kind in MEDIA_TYPES else None
        try:
            while True:
                msg = await ws.receive_json()
                if isinstance(msg, dict) and msg.get("type") == "event":
                    ev = {k: v for k, v in msg.items() if k != "type"}
                    spike.log({"client": client, **ev})
        except (WebSocketDisconnect, ValueError, RuntimeError):
            pass
        finally:
            spike.sockets.pop(ws, None)
            spike.log({"ev": "disconnect", "client": client})

    @contextlib.asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncGenerator[None]:
        task = asyncio.create_task(spike.broadcast_loop())
        yield
        task.cancel()

    return Starlette(
        routes=[
            Route("/", index),
            Route("/clips/{kind}/{name}", clip),
            Route("/health", health),
            WebSocketRoute("/ws", ws_endpoint),
        ],
        lifespan=lifespan,
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=8093)
    ap.add_argument("--run-name", default=time.strftime("run-%Y%m%d-%H%M%S"))
    ap.add_argument("--key", default=secrets.token_urlsafe(9))
    args = ap.parse_args()
    log_dir = LOG_ROOT / args.run_name
    spike = Spike(args.key, log_dir)
    print(f"clips: {len(spike.clips)}  log: {log_dir}")
    print(f"local:  http://127.0.0.1:{args.port}/?k={args.key}")
    print(f"phones: https://<tunnel-name>.trycloudflare.com/?k={args.key}")
    uvicorn.run(build_app(spike), host="127.0.0.1", port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
