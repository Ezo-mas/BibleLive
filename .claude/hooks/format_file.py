"""PostToolUse hook: format the file Claude just edited or wrote.

Python files go through ``ruff format``; ``frontend/src`` TS/TSX/CSS files go
through prettier, the same scopes as ``make format`` and ``npm run format``.
Other files are left alone. Tools come from the checkout's ``.venv`` and
``frontend/node_modules``, falling back to the main checkout
(``CLAUDE_PROJECT_DIR``) because worktrees have neither. A missing tool is not
an error: the hook stays silent and the normal checks still catch formatting.

Plain Python with no dependencies so it runs the same on Linux and Windows.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

PRETTIER_SUFFIXES = {".ts", ".tsx", ".css"}
TIMEOUT_S = 20


def _repo_root(path: Path) -> Path | None:
    # `.git` is a directory in the main checkout and a file in a worktree.
    for parent in path.parents:
        if (parent / ".git").exists():
            return parent
    return None


def _bases(root: Path) -> list[Path]:
    bases = [root]
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR")
    if project_dir and Path(project_dir).resolve() != root:
        bases.append(Path(project_dir).resolve())
    return bases


def _ruff(root: Path) -> str | None:
    for base in _bases(root):
        for candidate in (base / ".venv/bin/ruff", base / ".venv/Scripts/ruff.exe"):
            if candidate.is_file():
                return str(candidate)
    return shutil.which("ruff")


def _prettier(root: Path) -> list[str] | None:
    node = shutil.which("node")
    if node is None:
        return None
    for base in _bases(root):
        script = base / "frontend/node_modules/prettier/bin/prettier.cjs"
        if script.is_file():
            return [node, str(script)]
    return None


def _command(path: Path, root: Path) -> tuple[list[str], Path] | None:
    rel = path.relative_to(root)
    if path.suffix == ".py":
        ruff = _ruff(root)
        # --force-exclude keeps pyproject's extend-exclude in force for explicit paths.
        return ([ruff, "format", "--force-exclude", str(path)], root) if ruff else None
    if rel.parts[:2] == ("frontend", "src") and path.suffix in PRETTIER_SUFFIXES:
        prettier = _prettier(root)
        if prettier is None:
            return None
        return ([*prettier, "--write", "--log-level", "warn", str(path)], root / "frontend")
    return None


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return 0
    file_path = (payload.get("tool_input") or {}).get("file_path")
    if not file_path:
        return 0
    path = Path(file_path).resolve()
    root = _repo_root(path)
    if root is None or not path.is_file():
        return 0
    command = _command(path, root)
    if command is None:
        return 0
    args, cwd = command
    try:
        result = subprocess.run(
            args, cwd=cwd, capture_output=True, text=True, timeout=TIMEOUT_S, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"format hook: {exc}", file=sys.stderr)
        return 1
    if result.returncode != 0:
        # Non-blocking (not exit 2): a half-finished edit may not parse yet.
        print(f"format hook: {result.stderr.strip() or result.stdout.strip()}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
