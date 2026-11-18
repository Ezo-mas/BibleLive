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
    ChecksumError,
    _whisper_cached,
    check_sha256,
    download_speech_command,
    gpu_self_test_command,
)
from launcher.process import build_plan
from launcher.settings import SPEECH_ENGINES, LauncherSettings, load_settings, save_settings
from launcher.speech import (
    GGUF_MODELS,
    WHISPER_MODELS,
    SpeechChoice,
    cached_gguf_path,
    cached_whisper_dir,
    file_sha256,
    hub_cache_dir,
    physical_cores,
    resolve_speech,
    resolve_speech_engine,
    speech_threads,
    whisper_model_arg,
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


def _snapshot(models_dir: Path, repo_id: str, commit: str, *, main: bool) -> Path:
    """A snapshot folder in the hub layout huggingface_hub reads; ``main``: also refs/main."""
    repo = hub_cache_dir(models_dir) / ("models--" + repo_id.replace("/", "--"))
    if main:
        (repo / "refs").mkdir(parents=True, exist_ok=True)
        (repo / "refs" / "main").write_text(commit, encoding="utf-8")
    snapshot = repo / "snapshots" / commit
    snapshot.mkdir(parents=True, exist_ok=True)
    return snapshot


def _cache(models_dir: Path, model, *, pinned: bool = True) -> Path:
    """Write ``model`` into the hub cache: as the pinned download does (the pinned commit, no
    refs/main), or as a download by name before the pins did (refs/main -> another commit)."""
    if pinned:
        snapshot = _snapshot(models_dir, model.repo_id, model.revision, main=False)
    else:
        snapshot = _snapshot(models_dir, model.repo_id, "abc123", main=True)
    path = snapshot / model.filename
    path.write_bytes(b"GGUF")
    return path


def _cache_whisper(models_dir: Path, name: str) -> Path:
    pin = WHISPER_MODELS[name]
    snapshot = _snapshot(models_dir, pin.repo_id, pin.revision, main=False)
    for filename in pin.files:
        (snapshot / filename).write_bytes(b"x")
    return snapshot


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


def test_models_are_pinned_to_a_commit_and_a_sha256():
    for model in [*GGUF_MODELS.values(), *WHISPER_MODELS.values()]:
        assert len(model.revision) == 40 and int(model.revision, 16) >= 0
    hashes = [m.sha256 for m in GGUF_MODELS.values()]
    hashes += [h for m in WHISPER_MODELS.values() for h in m.files.values()]
    assert all(len(h) == 64 and int(h, 16) >= 0 for h in hashes)
    # Every file faster-whisper loads: the weights, the config and the tokenizer.
    for pin in WHISPER_MODELS.values():
        assert {"model.bin", "config.json", "tokenizer.json"} <= set(pin.files)


def test_an_older_download_still_runs_with_a_warning(tmp_path):
    # Downloaded by name before the pins: still used (the service keeps running), but the
    # operator is told to re-download the checked version.
    kmynas = _cache(tmp_path, KMYNAS, pinned=False)
    assert cached_gguf_path("kmynas", tmp_path, pinned_only=True) is None
    choice = resolve_speech("kmynas", tmp_path, NO_GPU)
    assert choice.engine == "kmynas" and Path(choice.model_paths["kmynas"]) == kmynas
    assert len(choice.warnings) == 1 and "--download-model" in choice.warnings[0]
    # The pinned download wins once it is there.
    pinned = _cache(tmp_path, KMYNAS)
    choice = resolve_speech("kmynas", tmp_path, NO_GPU)
    assert Path(choice.model_paths["kmynas"]) == pinned and choice.warnings == []


def test_whisper_runs_from_the_pinned_folder(tmp_path):
    assert whisper_model_arg("large-v3-turbo", tmp_path) == "large-v3-turbo"  # not downloaded
    folder = _cache_whisper(tmp_path, "large-v3-turbo")
    assert Path(cached_whisper_dir("large-v3-turbo", tmp_path)) == folder
    assert Path(whisper_model_arg("large-v3-turbo", tmp_path)) == folder
    assert _whisper_cached("large-v3-turbo", tmp_path) is True
    # A file missing from the pinned folder: load by name, as before the pins.
    (folder / "tokenizer.json").unlink()
    assert whisper_model_arg("large-v3-turbo", tmp_path) == "large-v3-turbo"
    # A model without a pin always goes by name.
    assert whisper_model_arg("tiny", tmp_path) == "tiny"


def test_launch_plan_passes_the_pinned_whisper_folder(tmp_path):
    folder = _cache_whisper(tmp_path / "models", "large-v3-turbo")
    plan = _plan(tmp_path, LauncherSettings(language="en", model="large-v3-turbo"), NO_GPU)
    assert Path(_arg(plan.audio_cmd, "--model")) == folder
    plan = _plan(tmp_path, LauncherSettings(language="en", model="small"), NO_GPU)
    assert _arg(plan.audio_cmd, "--model") == "small"


def test_checksum_mismatch_deletes_the_file(tmp_path):
    good = tmp_path / "good.bin"
    good.write_bytes(b"model")
    check_sha256(good, file_sha256(good))
    assert good.exists()
    blob = tmp_path / "blob"
    blob.write_bytes(b"tampered")
    link = tmp_path / "snapshot.bin"
    try:
        link.symlink_to(blob)
    except OSError:  # Windows without Developer Mode: the hub cache holds a copy instead
        link = blob
    with pytest.raises(ChecksumError, match="expected"):
        check_sha256(link, "0" * 64)
    assert not blob.exists() and not link.exists()


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

    def gguf(model, cache):
        calls.append(("gguf", model.repo_id, model.filename, cache))
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


def test_default_downloaders_fetch_the_pinned_commit_and_check_it(tmp_path, monkeypatch):
    import faster_whisper.utils
    import huggingface_hub
    from launcher import commands
    from launcher.speech import GgufModel, WhisperModel

    folder = tmp_path / "snap"
    folder.mkdir()
    (folder / "model.bin").write_bytes(b"weights")
    seen: dict = {}

    def fake_download_model(model, revision=None):
        seen["whisper"] = (model, revision)
        return str(folder)

    pin = WhisperModel("x/y", "c" * 40, {"model.bin": file_sha256(folder / "model.bin")})
    monkeypatch.setattr(faster_whisper.utils, "download_model", fake_download_model)
    monkeypatch.setitem(commands.WHISPER_MODELS, "small", pin)
    assert commands._default_downloader("small") == str(folder)
    assert seen["whisper"] == ("x/y", "c" * 40)
    assert commands._default_downloader("tiny") == str(folder)  # no pin: by name
    assert seen["whisper"] == ("tiny", None)
    monkeypatch.setitem(
        commands.WHISPER_MODELS, "small", WhisperModel("x/y", "c" * 40, {"model.bin": "0" * 64})
    )
    with pytest.raises(ChecksumError):
        commands._default_downloader("small")
    assert not (folder / "model.bin").exists()

    gguf_file = tmp_path / "m.gguf"
    gguf_file.write_bytes(b"GGUF")

    def fake_hf_hub_download(**kwargs):
        seen["gguf"] = kwargs
        return str(gguf_file)

    monkeypatch.setattr(huggingface_hub, "hf_hub_download", fake_hf_hub_download)
    model = GgufModel("a/b", "m.gguf", 1, "d" * 40, file_sha256(gguf_file))
    assert commands._default_gguf_downloader(model, tmp_path) == str(gguf_file)
    assert seen["gguf"]["revision"] == "d" * 40 and seen["gguf"]["filename"] == "m.gguf"


def test_whisper_pins_use_faster_whispers_repo_ids():
    # The cache folder is named after the repo id: the pin and faster-whisper must agree, or a
    # by-name load would look in another folder than the pinned download.
    from faster_whisper.utils import _MODELS

    for name, pin in WHISPER_MODELS.items():
        assert _MODELS[name] == pin.repo_id, name
