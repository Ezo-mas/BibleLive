"""Hardware probe: what is this machine, and can it run BibleLive's speech-to-text?

Standard library only (no psutil / py-cpuinfo), so it runs anywhere the project
runs and can later back the installer's hardware check. Linux reads /proc and
/sys; Windows uses ctypes. Anything it cannot determine is reported as None
rather than guessed.

    python benchmarks/hwprobe.py                 # human-readable summary
    python benchmarks/hwprobe.py --json out.json # also write the raw data
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

# Windows IsProcessorFeaturePresent() feature ids.
_PF_AVX2 = 40
_PF_AVX512F = 41


def parse_cpuinfo(text: str) -> dict[str, Any]:
    """Extract model, flags and core counts from the text of /proc/cpuinfo."""
    model: str | None = None
    flags: set[str] = set()
    cores: set[tuple[str, str]] = set()
    logical = 0
    physical_id = ""
    for line in text.splitlines():
        key, sep, value = line.partition(":")
        if not sep:
            continue
        key, value = key.strip(), value.strip()
        if key == "processor":
            logical += 1
        elif key == "model name" and model is None:
            model = value
        elif key == "flags" and not flags:
            flags = set(value.split())
        elif key == "physical id":
            physical_id = value
        elif key == "core id":
            cores.add((physical_id, value))
    return {
        "model": model,
        "flags": flags,
        "logical_cores": logical or None,
        "physical_cores": len(cores) or None,
    }


def _run(cmd: list[str]) -> str | None:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=10, check=True)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None


def _read(path: str) -> str | None:
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return None


def _windows_feature(feature: int) -> bool | None:
    try:
        import ctypes

        return bool(ctypes.windll.kernel32.IsProcessorFeaturePresent(feature))  # type: ignore[attr-defined,unused-ignore]
    except (AttributeError, OSError):
        return None


def _windows_ram_bytes() -> int | None:
    try:
        import ctypes

        class MemStatus(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = MemStatus()
        status.dwLength = ctypes.sizeof(MemStatus)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))  # type: ignore[attr-defined,unused-ignore]
        return int(status.ullTotalPhys)
    except (AttributeError, OSError):
        return None


def _windows_cpu_name() -> str | None:
    try:
        import winreg

        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
        )
        with key:
            return str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).strip() or None
    except (ImportError, OSError):
        return None


def probe_cpu() -> dict[str, Any]:
    info: dict[str, Any] = {
        "model": platform.processor() or None,
        "logical_cores": os.cpu_count(),
        "physical_cores": None,
        "avx2": None,
        "avx512": None,
        "vnni": None,
    }
    if sys.platform.startswith("linux"):
        text = _read("/proc/cpuinfo") or ""
        parsed = parse_cpuinfo(text)
        flags = parsed["flags"]
        info["model"] = parsed["model"] or info["model"]
        info["logical_cores"] = parsed["logical_cores"] or info["logical_cores"]
        info["physical_cores"] = parsed["physical_cores"]
        if flags:
            info["avx2"] = "avx2" in flags
            info["avx512"] = "avx512f" in flags
            info["vnni"] = bool(flags & {"avx512_vnni", "avx_vnni"})
    elif sys.platform == "win32":
        info["model"] = _windows_cpu_name() or info["model"]
        info["avx2"] = _windows_feature(_PF_AVX2)
        info["avx512"] = _windows_feature(_PF_AVX512F)
    return info


def probe_ram_bytes() -> int | None:
    if sys.platform.startswith("linux"):
        for line in (_read("/proc/meminfo") or "").splitlines():
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) * 1024
        return None
    if sys.platform == "win32":
        return _windows_ram_bytes()
    return None


def probe_power() -> dict[str, Any]:
    """Power profile / governor / AC state: all of them change sustained speed."""
    power: dict[str, Any] = {"profile": None, "governor": None, "on_ac": None}
    if not sys.platform.startswith("linux"):
        return power
    power["profile"] = _run(["powerprofilesctl", "get"])
    power["governor"] = _read("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor")
    online = [_read(str(p / "online")) for p in Path("/sys/class/power_supply").glob("A*")]
    online += [_read(str(p / "online")) for p in Path("/sys/class/power_supply").glob("AC*")]
    values = [v for v in online if v is not None]
    if values:
        power["on_ac"] = "1" in values
    return power


def probe_ffmpeg() -> dict[str, Any]:
    exe = shutil.which("ffmpeg")
    if exe is None:
        return {"path": None, "version": None, "mp3_decoder": None}
    first = (_run([exe, "-hide_banner", "-version"]) or "").splitlines()
    decoders = _run([exe, "-hide_banner", "-decoders"]) or ""
    return {
        "path": exe,
        "version": first[0] if first else None,
        "mp3_decoder": any(line.split()[1:2] == ["mp3"] for line in decoders.splitlines()),
    }


def probe_python_stack() -> dict[str, Any]:
    stack: dict[str, Any] = {
        "python": platform.python_version(),
        "ctranslate2": None,
        "faster_whisper": None,
        "cpu_compute_types": None,
        "webrtcvad": False,
    }
    try:
        import ctranslate2

        stack["ctranslate2"] = ctranslate2.__version__
        stack["cpu_compute_types"] = sorted(ctranslate2.get_supported_compute_types("cpu"))
    except Exception:  # missing or broken wheel: report absence, never crash the probe
        pass
    try:
        import faster_whisper

        stack["faster_whisper"] = getattr(faster_whisper, "__version__", "unknown")
    except Exception:
        pass
    try:
        import webrtcvad  # noqa: F401

        stack["webrtcvad"] = True
    except Exception:
        pass
    return stack


def collect() -> dict[str, Any]:
    ram = probe_ram_bytes()
    return {
        "host": platform.node(),
        "os": f"{platform.system()} {platform.release()}",
        "cpu": probe_cpu(),
        "ram_gb": round(ram / 1024**3, 1) if ram else None,
        "power": probe_power(),
        "ffmpeg": probe_ffmpeg(),
        "stack": probe_python_stack(),
    }


def verdicts(info: dict[str, Any]) -> list[str]:
    """Plain-language problems; an empty list means nothing blocks a benchmark run."""
    problems: list[str] = []
    cpu = info["cpu"]
    if cpu["avx2"] is False:
        problems.append("CPU has no AVX2: CTranslate2 falls back to a much slower path.")
    if info["ram_gb"] is not None and info["ram_gb"] < 7.5:
        problems.append(f"Only {info['ram_gb']} GB RAM: medium may not fit comfortably.")
    if info["ffmpeg"]["path"] is None:
        problems.append("ffmpeg not found on PATH.")
    elif info["ffmpeg"]["mp3_decoder"] is False:
        problems.append("ffmpeg has no MP3 decoder (benchmark audio 09-13.mp3 needs one).")
    stack = info["stack"]
    if stack["ctranslate2"] is None:
        problems.append("ctranslate2 is not importable in this Python.")
    if not stack["webrtcvad"]:
        problems.append("webrtcvad is not importable in this Python.")
    power = info["power"]
    if power["on_ac"] is False:
        problems.append("Running on battery: sustained speed will be lower and unrepresentative.")
    if power["profile"] == "power-saver":
        problems.append("Power profile is 'power-saver': switch to 'performance' or 'balanced'.")
    return problems


def render(info: dict[str, Any]) -> str:
    cpu, power, ff, stack = info["cpu"], info["power"], info["ffmpeg"], info["stack"]
    yes_no = {True: "yes", False: "NO", None: "unknown"}
    lines = [
        f"Host      : {info['host']} ({info['os']})",
        f"CPU       : {cpu['model']}",
        f"Cores     : {cpu['physical_cores'] or '?'} physical / {cpu['logical_cores'] or '?'} logical",
        f"RAM       : {info['ram_gb'] or '?'} GB",
        f"AVX2      : {yes_no[cpu['avx2']]}   AVX-512: {yes_no[cpu['avx512']]}"
        f"   VNNI: {yes_no[cpu['vnni']]}",
        f"Power     : profile={power['profile']} governor={power['governor']}"
        f" on_ac={power['on_ac']}",
        f"ffmpeg    : {ff['version']} (mp3 decoder: {yes_no[ff['mp3_decoder']]})",
        f"Python    : {stack['python']}  ctranslate2={stack['ctranslate2']}"
        f"  faster-whisper={stack['faster_whisper']}  webrtcvad={yes_no[stack['webrtcvad']]}",
        f"CPU types : {stack['cpu_compute_types']}",
    ]
    problems = verdicts(info)
    lines.append("Problems  : " + ("none" if not problems else ""))
    lines.extend(f"  - {p}" for p in problems)
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--json", metavar="PATH", help="also write the raw probe data as JSON")
    args = ap.parse_args()
    info = collect()
    print(render(info))
    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps(info, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
