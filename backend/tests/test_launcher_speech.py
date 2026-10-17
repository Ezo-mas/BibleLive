"""Launcher: Lithuanian speech model (Kmynas / Canary GGUF or Whisper).

GGUF models are faked as files in the Hugging Face hub layout; downloads and the GPU are injected.
Nothing here touches the network or needs a GPU.
"""

import io
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from launcher import i18n
from launcher.cli import apply_overrides, build_parser, format_plan
from launcher.commands import (
    EXIT_FAIL,
    EXIT_OK,
    _whisper_cached,
    download_speech_command,
    gpu_self_test_command,
)
from launcher.process import build_plan
from launcher.settings import SPEECH_ENGINES, LauncherSettings, load_settings, save_settings
from launcher.speech import (
    GGUF_MODELS,
    SpeechChoice,
    cached_gguf_path,
    hub_cache_dir,
    physical_cores,
    resolve_speech,
    resolve_speech_engine,
    speech_threads,
)
from launcher.viewmodel import (
    describe_speech,
    form_from_settings,
    settings_from_form,
    speech_engine_options,
)

NVIDIA = lambda: ["float16", "int8"]  # noqa: E731
NO_GPU = lambda: []  # noqa: E731
KMYNAS = GGUF_MODELS["kmynas"]
CANARY = GGUF_MODELS["canary"]


@pytest.fixture(autouse=True)
def _restore_hf_home(monkeypatch):
    # download_speech_command / the self-test set HF_HOME in os.environ; undo it after each test.
    monkeypatch.setenv("HF_HOME", "unset-by-test")


def _cache(models_dir: Path, model) -> Path:
    """Write ``model`` into the hub layout huggingface_hub reads (refs/main + snapshots)."""
    repo = hub_cache_dir(models_dir) / ("models--" + model.repo_id.replace("/", "--"))
    (repo / "refs").mkdir(parents=True, exist_ok=True)
    (repo / "refs" / "main").write_text("abc123", encoding="utf-8")
    snapshot = repo / "snapshots" / "abc123"
    snapshot.mkdir(parents=True, exist_ok=True)
    path = snapshot / model.filename
    path.write_bytes(b"GGUF")
    return path


def _plan(tmp_path, settings, probe):
    return build_plan(
        settings,
        app_root=tmp_path / "app",
        data_dir=tmp_path / "data",
        models_dir=tmp_path / "models",
        base_env={"PATH": "/usr/bin"},
        python="py",
        probe=probe,
    )


def _arg(cmd: list[str], flag: str) -> str:
    return cmd[cmd.index(flag) + 1]


# --- engine choice and fallback --------------------------------------------------------------


def test_auto_engine_follows_the_graphics_card():
    assert resolve_speech_engine("auto", NO_GPU) == "kmynas"
    assert resolve_speech_engine("auto", NVIDIA) == "kmynas+canary"
    assert resolve_speech_engine("whisper", NVIDIA) == "whisper"


def test_gguf_repo_ids_are_the_published_ones():
    assert KMYNAS.repo_id == "Ezomas/kmynas-parakeet-lt-v4-gguf"
    assert KMYNAS.filename == "kmynas-v4-Q8_0.gguf"
    assert CANARY.repo_id == "handy-computer/canary-1b-v2-gguf"
    assert CANARY.filename == "canary-1b-v2-Q8_0.gguf"


def test_cached_models_are_found(tmp_path):
    kmynas = _cache(tmp_path, KMYNAS)
    canary = _cache(tmp_path, CANARY)
    assert Path(cached_gguf_path("kmynas", tmp_path)) == kmynas
    choice = resolve_speech("kmynas+canary", tmp_path, NVIDIA)
    assert choice.engine == "kmynas+canary"
    assert {k: Path(v) for k, v in choice.model_paths.items()} == {
        "kmynas": kmynas,
        "canary": canary,
    }
    assert choice.warnings == []


def test_missing_models_fall_back_to_whisper(tmp_path):
    choice = resolve_speech("kmynas", tmp_path, NO_GPU)
    assert choice == SpeechChoice("whisper", {}, choice.warnings)
    assert choice.warnings and "--download-model" in choice.warnings[0]


def test_missing_canary_keeps_kmynas(tmp_path):
    kmynas = _cache(tmp_path, KMYNAS)
    choice = resolve_speech("kmynas+canary", tmp_path, NVIDIA)
    assert choice.engine == "kmynas"
    assert {k: Path(v) for k, v in choice.model_paths.items()} == {"kmynas": kmynas}
    assert choice.warnings and "canary" in choice.warnings[0]


def test_speech_threads_leave_one_core_capped_at_six(monkeypatch):
    assert 1 <= physical_cores() <= (os.cpu_count() or 1)
    monkeypatch.setattr("launcher.speech.physical_cores", lambda: 12)
    assert speech_threads() == 6
    monkeypatch.setattr("launcher.speech.physical_cores", lambda: 4)
    assert speech_threads() == 3  # the 4c/4t target PC: one core stays free
    monkeypatch.setattr("launcher.speech.physical_cores", lambda: 1)
    assert speech_threads() == 1


# --- command building ------------------------------------------------------------------------


def test_kmynas_on_the_processor_command(tmp_path):
    kmynas = _cache(tmp_path / "models", KMYNAS)
    plan = _plan(tmp_path, LauncherSettings(language="lt", speech_engine="kmynas"), NO_GPU)
    cmd = plan.audio_cmd
    assert plan.speech_engine == "kmynas"
    assert _arg(cmd, "--stt") == "kmynas"
    assert _arg(cmd, "--device") == "cpu"
    assert Path(_arg(cmd, "--kmynas-model")) == kmynas
    assert "--canary-model" not in cmd
    assert 1 <= int(_arg(cmd, "--cpu-threads")) <= 6
    assert (
        plan.warnings == []
    )  # Lithuanian on the processor is the designed path: no emergency note
    assert "speech: kmynas" in format_plan(plan)


def test_kmynas_canary_on_nvidia_command(tmp_path):
    kmynas = _cache(tmp_path / "models", KMYNAS)
    canary = _cache(tmp_path / "models", CANARY)
    plan = _plan(tmp_path, LauncherSettings(language="lt", speech_engine="auto"), NVIDIA)
    cmd = plan.audio_cmd
    assert plan.speech_engine == "kmynas+canary"
    assert _arg(cmd, "--stt") == "kmynas+canary"
    assert _arg(cmd, "--device") == "cuda"
    assert Path(_arg(cmd, "--kmynas-model")) == kmynas
    assert Path(_arg(cmd, "--canary-model")) == canary


def test_session_log_and_audio_flags(tmp_path):
    plan = _plan(tmp_path, LauncherSettings(), NO_GPU)
    cmd = plan.audio_cmd
    assert _arg(cmd, "--session-log-dir") == str(tmp_path / "data" / "logs" / "sessions")
    assert _arg(cmd, "--session-audio") == "on"
    off = _plan(tmp_path, LauncherSettings(session_audio=False), NO_GPU)
    assert _arg(off.audio_cmd, "--session-audio") == "off"


def test_whisper_engine_keeps_the_whisper_device_warnings(tmp_path):
    plan = _plan(tmp_path, LauncherSettings(language="en", speech_engine="whisper"), NO_GPU)
    assert _arg(plan.audio_cmd, "--stt") == "whisper"
    assert "--kmynas-model" not in plan.audio_cmd
    assert any("NVIDIA" in w for w in plan.warnings)


def test_english_keeps_whisper_device_warnings_even_with_kmynas(tmp_path):
    _cache(tmp_path / "models", KMYNAS)
    plan = _plan(tmp_path, LauncherSettings(language="en", speech_engine="kmynas"), NO_GPU)
    assert _arg(plan.audio_cmd, "--stt") == "kmynas"  # the engine still runs for Lithuanian
    assert any("NVIDIA" in w for w in plan.warnings)


def test_kmynas_canary_on_the_processor_warns(tmp_path):
    _cache(tmp_path / "models", KMYNAS)
    _cache(tmp_path / "models", CANARY)
    plan = _plan(tmp_path, LauncherSettings(language="lt", speech_engine="kmynas+canary"), NO_GPU)
    assert plan.speech_engine == "kmynas+canary"
    assert any("slow" in w for w in plan.warnings)


def test_speech_flags_match_the_audio_cli(tmp_path):
    pytest.importorskip("audio.cli")
    from launcher.speech import GGUF_LANGUAGES

    from audio.cli import build_parser as audio_parser
    from audio.stt_gguf import GGUF_LANGUAGES as AUDIO_GGUF_LANGUAGES
    from audio.stt_gguf import STT_ENGINES

    # The launcher offers a subset of the audio engines (not canary alone).
    assert set(SPEECH_ENGINES) - {"auto"} <= set(STT_ENGINES)
    assert GGUF_LANGUAGES == AUDIO_GGUF_LANGUAGES
    _cache(tmp_path / "models", KMYNAS)
    _cache(tmp_path / "models", CANARY)
    plan = _plan(tmp_path, LauncherSettings(language="lt", speech_engine="kmynas+canary"), NVIDIA)
    known = {opt for action in audio_parser()._actions for opt in action.option_strings}
    unknown = [t for t in plan.audio_cmd if t.startswith("--") and t not in known]
    assert unknown == []


# --- settings and CLI ------------------------------------------------------------------------


def test_new_settings_round_trip_and_invalid_values_fall_back(tmp_path):
    path = tmp_path / "launcher.json"
    s = LauncherSettings(speech_engine="kmynas+canary", session_audio=False)
    save_settings(s, path)
    assert load_settings(path) == s
    path.write_text('{"speech_engine": "gpt", "session_audio": "yes"}', encoding="utf-8")
    loaded = load_settings(path)
    assert loaded.speech_engine == "auto"
    assert loaded.session_audio is True


def test_cli_flags_for_speech():
    args = build_parser().parse_args(
        ["--speech-engine", "kmynas", "--no-whisper", "--session-audio", "off", "--download-model"]
    )
    assert args.speech_engine == "kmynas" and args.no_whisper and args.download_model
    merged = apply_overrides(LauncherSettings(), args)
    assert merged.speech_engine == "kmynas" and merged.session_audio is False
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--speech-engine", "gpt"])
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--session-audio", "maybe"])


def test_form_carries_the_speech_engine():
    s = LauncherSettings(speech_engine="kmynas")
    assert form_from_settings(s).speech_engine == "kmynas"
    assert settings_from_form(LauncherSettings(), form_from_settings(s)).speech_engine == "kmynas"


def test_speech_options_and_status_text():
    assert tuple(v for v, _ in speech_engine_options()) == SPEECH_ENGINES
    assert "Kmynas" in describe_speech("kmynas", "cpu", "int8")
    assert "processor" in describe_speech("kmynas", "cpu", "int8")
    assert "NVIDIA" in describe_speech("kmynas+canary", "cuda", "int8_float16")
    assert "float16" in describe_speech("whisper", "cuda", "float16")
    try:
        i18n.set_language("lt")
        assert "Lietuvių kalba" in describe_speech("kmynas", "cpu", "int8")
    finally:
        i18n.set_language("en")


# --- downloads -------------------------------------------------------------------------------


def test_download_fetches_gguf_then_whisper(tmp_path):
    calls: list[tuple] = []

    def gguf(repo, filename, cache):
        calls.append(("gguf", repo, filename, cache))
        return str(cache / "file.gguf")

    def whisper(model):
        calls.append(("whisper", model))
        return "/whisper"

    models = tmp_path / "models"
    common = {
        "whisper_downloader": whisper,
        "gguf_downloader": gguf,
        "out": io.StringIO(),
        "err": io.StringIO(),
        "progress_interval": 0.01,
    }
    code = download_speech_command(
        LauncherSettings(speech_engine="kmynas"), models, with_whisper=False, probe=NO_GPU, **common
    )
    assert code == EXIT_OK
    assert calls == [("gguf", KMYNAS.repo_id, KMYNAS.filename, hub_cache_dir(models))]

    calls.clear()
    settings = LauncherSettings(speech_engine="kmynas+canary", model="large-v3-turbo")
    code = download_speech_command(settings, models, probe=NVIDIA, **common)
    assert code == EXIT_OK
    assert [c[0] for c in calls] == ["gguf", "gguf", "whisper"]
    assert calls[1][2] == CANARY.filename and calls[-1] == ("whisper", "large-v3-turbo")
    assert "MODEL OK: kmynas" in common["out"].getvalue()


def test_download_failure_stops_and_reports(tmp_path):
    whisper_called = []

    def gguf(*_a):
        raise OSError("offline")

    err = io.StringIO()
    code = download_speech_command(
        LauncherSettings(speech_engine="kmynas+canary"),
        tmp_path,
        probe=NVIDIA,
        whisper_downloader=lambda m: whisper_called.append(m) or "",
        gguf_downloader=gguf,
        out=io.StringIO(),
        err=err,
        progress_interval=0.01,
    )
    assert code == EXIT_FAIL
    assert "offline" in err.getvalue()
    assert whisper_called == []


def test_whisper_cache_check_never_downloads(tmp_path):
    # local_files_only: an empty cache reports "not downloaded" without touching the network.
    assert _whisper_cached("small", tmp_path) is False


# --- self-test -------------------------------------------------------------------------------


def _silent_ok_loader(seen: dict):
    def loader(engine, paths, device, threads):
        seen.update(engine=engine, paths=paths, device=device, threads=threads)
        return lambda pcm: SimpleNamespace(failed=False, n=len(pcm))

    return loader


def test_selftest_kmynas_on_the_processor(tmp_path):
    models = tmp_path / "models"
    _cache(models, KMYNAS)
    seen: dict = {}
    out = io.StringIO()
    code = gpu_self_test_command(
        LauncherSettings(speech_engine="kmynas"),
        models,
        probe=NO_GPU,
        gguf_loader=_silent_ok_loader(seen),
        whisper_cached=lambda _m, _d: False,
        out=out,
    )
    assert code == EXIT_OK
    assert seen["engine"] == "kmynas" and seen["device"] == "cpu"
    assert set(seen["paths"]) == {"kmynas"} and 1 <= seen["threads"] <= 6
    assert "SELFTEST OK" in out.getvalue() and "engine=kmynas" in out.getvalue()


def test_selftest_fails_when_the_model_is_missing(tmp_path):
    out = io.StringIO()
    code = gpu_self_test_command(
        LauncherSettings(speech_engine="kmynas"),
        tmp_path / "models",
        probe=NO_GPU,
        gguf_loader=_silent_ok_loader({}),
        whisper_cached=lambda _m, _d: False,
        out=out,
    )
    assert code == EXIT_FAIL
    assert "kmynas model not downloaded" in out.getvalue()


def test_selftest_kmynas_canary_needs_nvidia(tmp_path):
    models = tmp_path / "models"
    _cache(models, KMYNAS)
    _cache(models, CANARY)
    loaded: list = []
    out = io.StringIO()
    code = gpu_self_test_command(
        LauncherSettings(speech_engine="kmynas+canary"),
        models,
        probe=NO_GPU,
        gguf_loader=lambda *a: loaded.append(a),
        whisper_cached=lambda _m, _d: False,
        out=out,
    )
    assert code == EXIT_FAIL and loaded == []
    assert "no usable CUDA GPU" in out.getvalue()


def test_selftest_reports_a_failed_transcription(tmp_path):
    models = tmp_path / "models"
    _cache(models, KMYNAS)
    code = gpu_self_test_command(
        LauncherSettings(speech_engine="kmynas"),
        models,
        probe=NO_GPU,
        gguf_loader=lambda *_a: lambda pcm: SimpleNamespace(failed=True),
        whisper_cached=lambda _m, _d: False,
        out=io.StringIO(),
    )
    assert code == EXIT_FAIL


def test_selftest_also_checks_whisper_when_it_is_downloaded(tmp_path):
    models = tmp_path / "models"
    _cache(models, KMYNAS)
    whisper_seen: list = []

    def whisper_loader(model, device, compute_type):
        whisper_seen.append((model, device, compute_type))
        return lambda pcm: SimpleNamespace(failed=False)

    out = io.StringIO()
    code = gpu_self_test_command(
        LauncherSettings(speech_engine="kmynas"),
        models,
        probe=NO_GPU,
        loader=whisper_loader,
        gguf_loader=_silent_ok_loader({}),
        whisper_cached=lambda _m, _d: True,
        out=out,
    )
    assert code == EXIT_OK
    assert whisper_seen == [("large-v3-turbo", "cpu", "int8")]
    assert out.getvalue().count("SELFTEST OK") == 2
