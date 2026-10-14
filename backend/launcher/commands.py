"""One-shot commands used by the installer: model download and GPU checks.

Each function returns a process exit code and takes its side effects (downloader, model
loader, probes, output) as parameters so the logic is testable without a GPU or network.
"""

import contextlib
import os
import subprocess
import sys
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from .gpu import (
    cuda_supported_compute_types,
    pick_compute_type,
    resolve_compute_type,
    resolve_device,
)
from .settings import LauncherSettings

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_NO_GPU = 3

# Approximate download sizes (MB) for progress percentages; unknown models show MB only.
_APPROX_MB = {"large-v3-turbo": 1620, "large-v3": 3100, "medium": 1530, "small": 480}


def apply_model_env(models_dir: Path, env: dict[str, str] | None = None) -> None:
    """Point huggingface_hub at ``models_dir``. Must run before it is first imported."""
    target = os.environ if env is None else env
    target["HF_HOME"] = str(models_dir)
    target.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")


def _dir_size_mb(path: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            with contextlib.suppress(OSError):
                total += os.path.getsize(os.path.join(root, name))
    return total // (1024 * 1024)


def _default_downloader(model: str) -> str:
    from faster_whisper.utils import download_model

    return str(download_model(model))


def download_model_command(
    model: str,
    models_dir: Path,
    *,
    downloader: Callable[[str], str] = _default_downloader,
    out: TextIO | None = None,
    err: TextIO | None = None,
    progress_interval: float = 2.0,
) -> int:
    """Download ``model`` into ``models_dir`` (idempotent), printing progress lines."""
    out = out or sys.stdout
    err = err or sys.stderr
    models_dir.mkdir(parents=True, exist_ok=True)
    apply_model_env(models_dir)
    approx = _APPROX_MB.get(model)
    print(f"Downloading model '{model}' into {models_dir} ...", file=out, flush=True)

    done = threading.Event()

    def report() -> None:
        while not done.wait(progress_interval):
            mb = _dir_size_mb(models_dir)
            suffix = f" / ~{approx} MB ({min(99, mb * 100 // approx)}%)" if approx else ""
            print(f"  progress: {mb} MB{suffix}", file=out, flush=True)

    reporter = threading.Thread(target=report, daemon=True)
    reporter.start()
    try:
        path = downloader(model)
    except Exception as exc:  # network, disk, invalid model name: report, exit 1
        print(f"MODEL DOWNLOAD FAILED: {type(exc).__name__}: {exc}", file=err, flush=True)
        return EXIT_FAIL
    finally:
        done.set()
        reporter.join(timeout=5)
    print(f"MODEL OK: {model} at {path} ({_dir_size_mb(models_dir)} MB)", file=out, flush=True)
    return EXIT_OK


# Minimum hardware. float16 in CTranslate2 needs compute capability 7.0 (GTX 16xx / RTX 20xx and
# newer); 4 GiB is the absolute floor for large-v3-turbo, 6 GiB leaves room for OBS (NVENC) on
# the same PC. Keep in step with scripts/windows/installer/BibleLive.iss.
MIN_COMPUTE_CAP = 7.0
MIN_VRAM_MIB = 4096
RECOMMENDED_VRAM_MIB = 6144


@dataclass(frozen=True)
class GpuInfo:
    """One row of nvidia-smi; ``compute_cap`` / ``memory_mib`` are None when not reported."""

    name: str
    driver: str
    compute_cap: float | None = None
    memory_mib: int | None = None


def _parse_float(text: str) -> float | None:
    try:
        return float(text)
    except ValueError:
        return None


def _parse_int(text: str) -> int | None:
    try:
        return int(float(text))
    except ValueError:
        return None


def parse_gpu_rows(text: str) -> list[GpuInfo]:
    """Parse ``--query-gpu=name,driver_version,compute_cap,memory.total`` csv (noheader, nounits).

    Also accepts the old two-column ``name,driver_version`` output and ``[N/A]`` / ``[Not
    Supported]`` cells (-> None). Rows that cannot be parsed are skipped.
    """
    rows: list[GpuInfo] = []
    for line in text.splitlines():
        cells = [c.strip() for c in line.split(",")]
        if len(cells) < 2 or not cells[0]:
            continue
        if len(cells) >= 4:
            name = ",".join(cells[:-3])  # a name with a comma stays intact
            driver, cc, mem = cells[-3:]
        else:
            name, driver, cc, mem = cells[0], cells[1], "", ""
        rows.append(GpuInfo(name, driver, _parse_float(cc), _parse_int(mem)))
    return rows


def query_gpus(
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> list[GpuInfo]:
    """All GPUs from nvidia-smi. Drivers that do not know ``compute_cap`` fail that query, so it
    falls back to name + driver only (the capability then stays None). [] if nvidia-smi is absent."""
    for fields in ("name,driver_version,compute_cap,memory.total", "name,driver_version"):
        try:
            proc = run(
                ["nvidia-smi", f"--query-gpu={fields}", "--format=csv,noheader,nounits"],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return []
        rows = parse_gpu_rows(proc.stdout) if proc.returncode == 0 else []
        if rows:
            return rows
    return []


def _meets_minimum(gpu: GpuInfo) -> bool:
    """Unknown values do not fail the check; the GPU self-test catches real problems."""
    return (gpu.compute_cap is None or gpu.compute_cap >= MIN_COMPUTE_CAP) and (
        gpu.memory_mib is None or gpu.memory_mib >= MIN_VRAM_MIB
    )


def best_gpu(gpus: list[GpuInfo]) -> GpuInfo | None:
    """The best qualifying GPU (most VRAM); if none qualifies, the strongest one."""
    if not gpus:
        return None

    return max(gpus, key=lambda g: (_meets_minimum(g), g.memory_mib or 0, g.compute_cap or 0.0))


def describe_gpu(gpu: GpuInfo | None) -> str:
    if gpu is None:
        return "nvidia-smi: not found"
    cc = f"{gpu.compute_cap:.1f}" if gpu.compute_cap is not None else "unknown"
    mem = f"{gpu.memory_mib} MiB" if gpu.memory_mib is not None else "unknown"
    return f"nvidia-smi: {gpu.name}, driver {gpu.driver}, compute capability {cc}, VRAM {mem}"


def check_gpu_command(
    *,
    probe: Callable[[], Iterable[str]] = cuda_supported_compute_types,
    smi: Callable[[], list[GpuInfo]] = query_gpus,
    out: TextIO | None = None,
) -> int:
    """Fast check: exit 0 if ctranslate2 sees a usable CUDA GPU that meets the minimum
    (compute capability 7.0+, 4 GiB VRAM), else 3. Under 6 GiB it still passes with a warning."""
    out = out or sys.stdout
    gpu = best_gpu(smi())
    desc = describe_gpu(gpu)
    supported = list(probe())
    best = pick_compute_type(supported)
    if best is None:
        print(f"GPU NOT AVAILABLE: no usable CUDA GPU for ctranslate2 ({desc})", file=out)
        return EXIT_NO_GPU
    if gpu is not None and not _meets_minimum(gpu):
        print(
            f"GPU TOO OLD OR TOO SMALL: needs compute capability {MIN_COMPUTE_CAP} or newer "
            f"(GTX 1660 / RTX 2060+) and at least {MIN_VRAM_MIB // 1024} GB VRAM ({desc})",
            file=out,
        )
        return EXIT_NO_GPU
    print(
        f"GPU OK: usable CUDA GPU, compute types {sorted(supported)}, best {best} ({desc})",
        file=out,
    )
    if gpu is not None and gpu.memory_mib is not None and gpu.memory_mib < RECOMMENDED_VRAM_MIB:
        print(
            f"WARNING: under {RECOMMENDED_VRAM_MIB // 1024} GB VRAM; running OBS streaming "
            "(NVENC) on the same PC may stutter. 6 GB or more is recommended.",
            file=out,
        )
    return EXIT_OK


def _default_model_loader(model: str, device: str, compute_type: str) -> Callable[[bytes], object]:
    # audio.stt registers the pip-installed NVIDIA DLL dirs before ctranslate2 loads, exactly
    # as the real pipeline does; SpeechToText is the pipeline's own model construction.
    from audio.stt import SpeechToText

    stt = SpeechToText(model_size=model, device=device, compute_type=compute_type)
    return stt.transcribe_utterance


def gpu_self_test_command(
    settings: LauncherSettings,
    models_dir: Path,
    *,
    probe: Callable[[], Iterable[str]] = cuda_supported_compute_types,
    loader: Callable[[str, str, str], Callable[[bytes], object]] = _default_model_loader,
    out: TextIO | None = None,
) -> int:
    """Load the resolved model on the resolved device and transcribe 1 s of silence."""
    out = out or sys.stdout
    apply_model_env(models_dir)
    device, _warnings = resolve_device(settings.device, probe)
    compute_type = resolve_compute_type(device, settings.compute_type, probe)
    tag = f"model={settings.model} device={device} compute_type={compute_type}"
    if device != "cuda" and settings.device != "cpu":
        print(f"SELFTEST FAIL: no usable CUDA GPU ({tag})", file=out, flush=True)
        return EXIT_FAIL
    try:
        transcribe = loader(settings.model, device, compute_type)
        result = transcribe(b"\x00\x00" * 16000)  # 1 s of 16 kHz mono s16 silence
    except Exception as exc:
        print(f"SELFTEST FAIL: {type(exc).__name__}: {exc} ({tag})", file=out, flush=True)
        return EXIT_FAIL
    if getattr(result, "failed", False):
        print(f"SELFTEST FAIL: transcription raised ({tag})", file=out, flush=True)
        return EXIT_FAIL
    print(f"SELFTEST OK: {tag}", file=out, flush=True)
    return EXIT_OK
