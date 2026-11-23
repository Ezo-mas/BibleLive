"""Send recorded session events to an already-running local BibleLive server."""

import argparse
import json
import math
import os
import sys
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

from core.server.models import ReferenceSubmitRequest, TranscriptSubmitRequest


@dataclass(frozen=True)
class Event:
    line: int
    at_s: float
    route: str
    body: dict[str, Any]


def read_events(lines: Iterable[str]) -> list[Event]:
    """Validate the whole file before sending anything, retaining log order."""
    events: list[Event] = []
    at_s = 0.0
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
            if not isinstance(record, dict):
                raise ValueError
            kind = record.get("type")
            if kind not in ("utterance", "shutdown_flush"):
                continue

            # End of speech is the closest available position to final delivery.
            # Untimed events and older positions follow the preceding event.
            for field in ("end_s", "start_s", "at_s"):
                position = record.get(field)
                if position is not None:
                    if type(position) not in (int, float) or not math.isfinite(position):
                        raise ValueError
                    at_s = max(at_s, position)
                    break

            if kind == "utterance":
                for transcript in record.get("transcripts", []):
                    if (
                        transcript.get("text")
                        and transcript.get("low_confidence_reason") is None
                        and not transcript.get("failed")
                    ):
                        body = TranscriptSubmitRequest(
                            text=transcript["text"], language=record["language"]
                        ).model_dump()
                        events.append(Event(line_number, at_s, "/api/transcript", body))
                        break

            for reference in record.get("references", []):
                # The endpoint resolves text from its own Bible. The log also
                # carries metadata that is only useful for detection comparisons.
                body = ReferenceSubmitRequest.model_validate(reference).model_dump(exclude={"text"})
                events.append(Event(line_number, at_s, "/api/reference", body))
        except (AttributeError, KeyError, OverflowError, TypeError, ValueError):
            raise ValueError(f"invalid session record at line {line_number}") from None
    return events


def replay(events: list[Event], port: int, speed: int, token: str | None) -> None:
    """Send the first event immediately and scale the gaps between events."""
    origin_s = events[0].at_s
    start = time.monotonic()
    with requests.Session() as session:
        session.trust_env = False  # Keep transcripts and credentials off configured proxies.
        if token:
            session.headers["Authorization"] = f"Bearer {token}"
        for event in events:
            remaining = (event.at_s - origin_s) / speed - (time.monotonic() - start)
            if remaining > 0:
                time.sleep(remaining)
            try:
                response = session.post(
                    f"http://127.0.0.1:{port}{event.route}",
                    json=event.body,
                    timeout=5,
                    allow_redirects=False,
                )
            except requests.RequestException:
                raise ValueError(f"line {event.line}: could not POST {event.route}") from None
            if not 200 <= response.status_code < 300:
                message = (
                    f"line {event.line}: POST {event.route} returned HTTP {response.status_code}"
                )
                if response.status_code == 401:
                    message += ". Check BIBLELIVE_OPERATOR_TOKEN"
                raise ValueError(message)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session", type=Path, help="recorded session .jsonl file")
    parser.add_argument("--speed", type=int, choices=(1, 10), default=1, help="playback multiplier")
    parser.add_argument(
        "--port", type=int, default=8000, help="local BibleLive port (default 8000)"
    )
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    try:
        with args.session.open(encoding="utf-8") as lines:
            events = read_events(lines)
        if not events:
            raise ValueError("the session contains no replayable events")
        replay(events, args.port, args.speed, os.environ.get("BIBLELIVE_OPERATOR_TOKEN"))
    except (OSError, ValueError) as error:
        print(f"Replay stopped: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Replay stopped", file=sys.stderr)
        return 130
    print(f"Replayed {len(events)} event(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
