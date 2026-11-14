"""Package version and optional installer stamp in ``core/build-id.txt``."""

import tomllib
from functools import lru_cache
from importlib import metadata
from pathlib import Path

_PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"
_BUILD_ID = Path(__file__).with_name("build-id.txt")


def package_version() -> str:
    """Read installed metadata, or the source tree's project version when unpackaged."""
    try:
        return metadata.version("biblelive")
    except metadata.PackageNotFoundError:
        try:
            with _PYPROJECT.open("rb") as source:
                value = tomllib.load(source)["project"]["version"]
            return value if isinstance(value, str) else "unknown"
        except (OSError, KeyError, tomllib.TOMLDecodeError):
            return "unknown"


@lru_cache(maxsize=1)
def version_string() -> str:
    """Return the installer stamp, or the package version when it is absent."""
    try:
        stamp = _BUILD_ID.read_text(encoding="utf-8").strip()
    except (OSError, ValueError):
        stamp = ""
    return stamp or package_version()
