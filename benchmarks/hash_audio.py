"""Hash audio files for the benchmark corpus manifest.

Usage:
    python benchmarks/hash_audio.py path/to/file1.wav path/to/file2.mp3

Prints `filename  sha256` per file to paste into benchmarks/corpus/manifest.json.
"""

import hashlib
import sys
from pathlib import Path


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    for arg in sys.argv[1:]:
        p = Path(arg)
        if not p.is_file():
            print(f"{p}  MISSING", file=sys.stderr)
            continue
        print(f"{p.name}  {sha256_file(p)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
