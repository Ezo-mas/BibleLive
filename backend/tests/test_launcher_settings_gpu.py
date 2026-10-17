"""Launcher settings persistence and GPU/compute-type selection."""

import json

from launcher.gpu import pick_compute_type, resolve_compute_type, resolve_device
from launcher.settings import LauncherSettings, load_settings, save_settings


def test_settings_round_trip(tmp_path):
    path = tmp_path / "sub" / "launcher.json"
    s = LauncherSettings(language="lt", input_device="Mic (X)", port=9001, extra_audio_args=["a"])
    save_settings(s, path)
    assert load_settings(path) == s


def test_missing_file_gives_defaults(tmp_path):
    assert load_settings(tmp_path / "nope.json") == LauncherSettings()


def test_corrupt_file_gives_defaults(tmp_path, caplog):
    path = tmp_path / "launcher.json"
    path.write_text("{not json", encoding="utf-8")
    assert load_settings(path) == LauncherSettings()
    assert "Ignoring" in caplog.text


def test_non_object_and_bad_types_fall_back(tmp_path):
    path = tmp_path / "launcher.json"
    path.write_text("[1]", encoding="utf-8")
    assert load_settings(path) == LauncherSettings()
    path.write_text(json.dumps({"port": "abc", "language": "lt", "unknown": 1}), encoding="utf-8")
    loaded = load_settings(path)
    assert loaded.port == 8000
    assert loaded.language == "lt"


def test_pick_compute_type_preference():
    assert pick_compute_type({"int8", "float16", "float32"}) == "float16"
    assert pick_compute_type({"int8", "int8_float16", "float32"}) == "int8_float16"
    assert pick_compute_type(["int8", "float32"]) == "int8"
    assert pick_compute_type(["float32"]) is None
    assert pick_compute_type([]) is None


def test_auto_device_falls_back_to_cpu_with_warning():
    device, warnings = resolve_device("auto", lambda: [])
    assert device == "cpu"
    assert warnings
    assert resolve_compute_type("cpu", "auto", lambda: []) == "int8"


def test_auto_device_picks_cuda():
    probe = lambda: ["int8", "int8_float16"]  # noqa: E731
    assert resolve_device("auto", probe) == ("cuda", [])
    assert resolve_compute_type("cuda", "auto", probe) == "int8_float16"


def test_explicit_cpu_warns_and_explicit_compute_type_wins():
    device, warnings = resolve_device("cpu", lambda: ["float16"])
    assert device == "cpu" and warnings
    assert resolve_compute_type("cuda", "float32", lambda: ["float16"]) == "float32"


def test_forced_cuda_without_gpu_warns():
    device, warnings = resolve_device("cuda", lambda: [])
    assert device == "cuda" and warnings


# --- installer commands -----------------------------------------------------------------


def test_build_plan_sets_hf_home(tmp_path):
    from launcher.process import build_plan

    plan = build_plan(
        LauncherSettings(), app_root=tmp_path, data_dir=tmp_path, models_dir=tmp_path / "m",
        base_env={}, probe=lambda: [],
    )  # fmt: skip
    assert plan.env["HF_HOME"] == str(tmp_path / "m")


def test_models_dir_uses_localappdata(monkeypatch, tmp_path):
    import sys

    from launcher.settings import models_dir

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert models_dir() == tmp_path / "BibleLive" / "models"


def test_apply_model_env():
    from pathlib import Path

    from launcher.commands import apply_model_env

    env: dict[str, str] = {}
    apply_model_env(Path("X"), env)
    assert env["HF_HOME"] == "X"


def test_cli_parses_oneshot_flags():
    from launcher.cli import build_parser

    a = build_parser().parse_args(["--download-model", "--model", "small"])
    assert a.download_model and a.model == "small" and not a.check_gpu
    assert build_parser().parse_args(["--gpu-self-test"]).gpu_self_test
    assert build_parser().parse_args(["--check-gpu"]).check_gpu


def test_download_model_ok_and_idempotent(tmp_path, capsys, monkeypatch):
    import io

    from launcher.commands import download_model_command

    monkeypatch.delenv("HF_HOME", raising=False)
    out = io.StringIO()
    calls: list[str] = []
    for _ in range(2):
        code = download_model_command(
            "large-v3-turbo", tmp_path / "models",
            downloader=lambda m: calls.append(m) or "/p", out=out, progress_interval=0.01,
        )  # fmt: skip
        assert code == 0
    assert calls == ["large-v3-turbo"] * 2
    assert "MODEL OK" in out.getvalue()
    assert (tmp_path / "models").is_dir()
    import os

    assert os.environ["HF_HOME"] == str(tmp_path / "models")


def test_download_model_failure_exit_1(tmp_path):
    import io

    from launcher.commands import download_model_command

    def boom(_m):
        raise OSError("offline")

    err = io.StringIO()
    assert (
        download_model_command("small", tmp_path, downloader=boom, out=io.StringIO(), err=err) == 1
    )
    assert "offline" in err.getvalue()


def test_check_gpu_exit_codes():
    import io

    from launcher.commands import GpuInfo, check_gpu_command

    out = io.StringIO()
    assert (
        check_gpu_command(
            probe=lambda: ["float16", "int8"],
            smi=lambda: [GpuInfo("RTX", "5xx", 8.9, 16303)],
            out=out,
        )
        == 0
    )
    assert "GPU OK" in out.getvalue() and "RTX" in out.getvalue()
    out = io.StringIO()
    assert check_gpu_command(probe=lambda: [], smi=lambda: [], out=out) == 3
    assert "NOT AVAILABLE" in out.getvalue()


def test_parse_gpu_rows_variants():
    from launcher.commands import GpuInfo, parse_gpu_rows

    full = "NVIDIA GeForce RTX 5070 Ti, 616.92, 12.0, 16303\nNVIDIA GeForce GTX 1050, 616.92, 6.1, 2048\n"
    assert parse_gpu_rows(full) == [
        GpuInfo("NVIDIA GeForce RTX 5070 Ti", "616.92", 12.0, 16303),
        GpuInfo("NVIDIA GeForce GTX 1050", "616.92", 6.1, 2048),
    ]
    # Old driver: two columns only -> capability and VRAM unknown.
    assert parse_gpu_rows("GeForce GTX 960, 516.94\n") == [GpuInfo("GeForce GTX 960", "516.94")]
    # [N/A] cells, blank lines, garbage and the old-driver error text are tolerated.
    assert parse_gpu_rows("RTX, 551.23, [N/A], [N/A]\n\nnonsense\n") == [
        GpuInfo("RTX", "551.23", None, None)
    ]
    assert parse_gpu_rows('Field "compute_cap" is not a valid field to query.\n') == []
    assert parse_gpu_rows("") == []


def test_query_gpus_falls_back_for_old_drivers():
    import subprocess

    from launcher.commands import GpuInfo, query_gpus

    calls = []

    def old_driver(cmd, **k):
        calls.append(cmd[1])
        if "compute_cap" in cmd[1]:
            return subprocess.CompletedProcess(cmd, 2, stdout="", stderr="invalid field")
        return subprocess.CompletedProcess(cmd, 0, stdout="GeForce GTX 960, 516.94\n", stderr="")

    assert query_gpus(old_driver) == [GpuInfo("GeForce GTX 960", "516.94")]
    assert len(calls) == 2

    def missing(*a, **k):
        raise FileNotFoundError

    assert query_gpus(missing) == []


def test_best_gpu_prefers_qualifying_with_most_vram():
    from launcher.commands import GpuInfo, best_gpu

    old = GpuInfo("GTX 1080", "551", 6.1, 8192)
    small = GpuInfo("GTX 1650", "551", 7.5, 2048)
    good = GpuInfo("RTX 2060", "551", 7.5, 6144)
    big = GpuInfo("RTX 4090", "551", 8.9, 24576)
    assert best_gpu([old, small, good]) == good
    assert best_gpu([good, big]) == big
    assert best_gpu([old, small]) in (old, small)  # nothing qualifies: still returns one
    assert best_gpu([]) is None


def test_check_gpu_enforces_minimum_hardware():
    import io

    from launcher.commands import GpuInfo, check_gpu_command

    ok_probe = lambda: ["float16", "int8"]  # noqa: E731

    def run(gpu):
        out = io.StringIO()
        return check_gpu_command(probe=ok_probe, smi=lambda: gpu, out=out), out.getvalue()

    code, text = run([GpuInfo("GTX 1080", "551", 6.1, 8192)])  # CC too low
    assert code == 3 and "TOO OLD" in text and "GTX 1080" in text
    code, text = run([GpuInfo("RTX 2060", "551", 7.5, 3072)])  # VRAM too low
    assert code == 3 and "TOO OLD OR TOO SMALL" in text
    code, text = run([GpuInfo("GTX 1660", "551", 7.5, 4096)])  # passes with a warning
    assert code == 0 and "GPU OK" in text and "WARNING" in text and "OBS" in text
    code, text = run([GpuInfo("RTX 3060", "551", 8.6, 12288)])  # fine, no warning
    assert code == 0 and "WARNING" not in text and "compute capability 8.6" in text
    code, text = run([GpuInfo("GeForce GTX 960", "516.94")])  # unknown values do not block
    assert code == 0 and "unknown" in text
    code, text = run([])  # nvidia-smi missing but ctranslate2 works
    assert code == 0 and "not found" in text


def test_gpu_self_test_ok_and_failures(tmp_path):
    import io
    from types import SimpleNamespace

    from launcher.commands import gpu_self_test_command

    seen: dict = {}

    def loader(model, device, ct):
        seen["args"] = (model, device, ct)
        return lambda pcm: SimpleNamespace(failed=False, n=len(pcm))

    out = io.StringIO()
    s = LauncherSettings(
        speech_engine="whisper"
    )  # the Whisper path; GGUF is in test_launcher_speech
    assert (
        gpu_self_test_command(s, tmp_path, probe=lambda: ["float16"], loader=loader, out=out) == 0
    )
    assert seen["args"] == ("large-v3-turbo", "cuda", "float16")
    assert "SELFTEST OK" in out.getvalue() and "float16" in out.getvalue()

    # auto with no GPU: fail without loading anything
    out = io.StringIO()
    assert gpu_self_test_command(s, tmp_path, probe=lambda: [], loader=loader, out=out) == 1
    assert "SELFTEST FAIL" in out.getvalue()

    # loader raising
    def bad(*a):
        raise RuntimeError("cublas64_12.dll not found")

    out = io.StringIO()
    assert gpu_self_test_command(s, tmp_path, probe=lambda: ["float16"], loader=bad, out=out) == 1
    assert "cublas" in out.getvalue()

    # transcription reported as failed
    out = io.StringIO()
    failing = lambda *a: lambda pcm: SimpleNamespace(failed=True)  # noqa: E731
    assert (
        gpu_self_test_command(s, tmp_path, probe=lambda: ["float16"], loader=failing, out=out) == 1
    )
