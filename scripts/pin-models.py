"""Print the pins for the speech models the launcher downloads (backend/launcher/speech.py).

Run it to re-pin after a model repo changes: it resolves each repo's current ``main`` to a
commit and prints the SHA256 of every file the launcher downloads from it. Large (LFS) files
take the hash the Hub publishes; small files are downloaded and hashed here. Paste the output
over the matching entries in ``GGUF_MODELS`` / ``WHISPER_MODELS`` and check the diff: a changed
hash means a changed model, which is a detection-accuracy change (AGENTS.md: benchmark it).

Usage: python scripts/pin-models.py   (standard library only, needs network access)
"""

import fnmatch
import hashlib
import json
import sys
import urllib.parse
import urllib.request

HUB = "https://huggingface.co"
GGUF = {
    "kmynas": ("Ezomas/kmynas-parakeet-lt-v4-gguf", "kmynas-v4-Q8_0.gguf"),
    "canary": ("handy-computer/canary-1b-v2-gguf", "canary-1b-v2-Q8_0.gguf"),
}
# Keep the repo ids faster-whisper uses (faster_whisper.utils._MODELS): the Hub cache folder is
# named after the id that was asked for, so a renamed repo keeps its old id here (the Hub
# redirects it) and existing downloads stay valid.
WHISPER = {
    "small": "Systran/faster-whisper-small",
    "medium": "Systran/faster-whisper-medium",
    "large-v3": "Systran/faster-whisper-large-v3",
    "large-v3-turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
}
# faster_whisper.utils.download_model's allow_patterns.
WHISPER_PATTERNS = (
    "config.json",
    "preprocessor_config.json",
    "model.bin",
    "tokenizer.json",
    "vocabulary.*",
)


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "biblelive-pin-models"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return bytes(resp.read())


def _tree(repo: str) -> tuple[str, dict[str, dict]]:
    commit = json.loads(_get(f"{HUB}/api/models/{repo}/revision/main"))["sha"]
    entries = json.loads(_get(f"{HUB}/api/models/{repo}/tree/{commit}"))
    return commit, {e["path"]: e for e in entries if e.get("type") == "file"}


def _sha256(repo: str, commit: str, entry: dict) -> str:
    lfs = entry.get("lfs")
    if lfs and lfs.get("oid"):
        return str(lfs["oid"])
    path = urllib.parse.quote(entry["path"])
    return hashlib.sha256(_get(f"{HUB}/{repo}/resolve/{commit}/{path}")).hexdigest()


def main() -> int:
    print("# GGUF_MODELS")
    for name, (repo, filename) in GGUF.items():
        commit, files = _tree(repo)
        print(f'    "{name}": "{repo}" @ {commit}')
        print(f"        {filename}: {_sha256(repo, commit, files[filename])}")
    print("# WHISPER_MODELS")
    for name, repo in WHISPER.items():
        commit, files = _tree(repo)
        print(f'    "{name}": "{repo}" @ {commit}')
        for path in sorted(files):
            if any(fnmatch.fnmatch(path, p) for p in WHISPER_PATTERNS):
                print(f"        {path}: {_sha256(repo, commit, files[path])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
