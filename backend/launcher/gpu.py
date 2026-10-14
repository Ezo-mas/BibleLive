"""CUDA detection and compute-type selection (degrades gracefully without ctranslate2)."""

import logging
from collections.abc import Callable, Iterable

logger = logging.getLogger(__name__)

# Best first: float16 is the accurate/fast choice on RTX 20xx+, the int8 variants
# are fallbacks for older cards.
CUDA_PREFERENCE = ("float16", "int8_float16", "int8")
CPU_COMPUTE_TYPE = "int8"

SupportedProbe = Callable[[], Iterable[str]]


def cuda_supported_compute_types() -> list[str]:
    """Compute types the GPU supports; empty if no usable CUDA device or no ctranslate2."""
    try:
        import ctranslate2  # type: ignore[import-not-found,import-untyped,unused-ignore]

        return sorted(ctranslate2.get_supported_compute_types("cuda"))
    except Exception as exc:  # ImportError, or ctranslate2 raising without a driver
        logger.info("CUDA not available: %s", exc)
        return []


def pick_compute_type(supported: Iterable[str]) -> str | None:
    """Best CUDA compute type from ``supported`` (None if none of the preferred ones)."""
    available = set(supported)
    for candidate in CUDA_PREFERENCE:
        if candidate in available:
            return candidate
    return None


def resolve_device(
    requested: str, probe: SupportedProbe = cuda_supported_compute_types
) -> tuple[str, list[str]]:
    """Resolve ``auto|cuda|cpu`` to a concrete device plus warnings for the user."""
    warnings: list[str] = []
    if requested == "cpu":
        warnings.append(
            "Running on CPU (emergency mode): transcription will be slow and less accurate."
        )
        return "cpu", warnings
    has_cuda = pick_compute_type(probe()) is not None
    if requested == "cuda":
        if not has_cuda:
            warnings.append("--device cuda requested but no usable CUDA GPU was detected.")
        return "cuda", warnings
    if has_cuda:
        return "cuda", warnings
    warnings.append(
        "No usable NVIDIA GPU detected; falling back to CPU (emergency mode, slow). "
        "Install an NVIDIA driver or pass --device cuda to force it."
    )
    return "cpu", warnings


def resolve_compute_type(
    device: str, requested: str, probe: SupportedProbe = cuda_supported_compute_types
) -> str:
    """Concrete compute type: an explicit request wins, else auto-pick for the device."""
    if requested != "auto":
        return requested
    if device == "cpu":
        return CPU_COMPUTE_TYPE
    return pick_compute_type(probe()) or CPU_COMPUTE_TYPE
