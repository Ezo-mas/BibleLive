"""Tests for the audio pipeline CLI arguments."""

import pytest

from audio.cli import WHISPER_MODEL_NAMES, build_parser

# Requires the optional audio extras (faster-whisper).
pytestmark = pytest.mark.audio


def test_every_model_name_is_one_faster_whisper_knows():
    """A name the installed faster-whisper cannot resolve fails only at load."""
    from faster_whisper.utils import available_models

    assert set(WHISPER_MODEL_NAMES) <= set(available_models())
    assert "large-v3-turbo" in WHISPER_MODEL_NAMES


def test_model_takes_a_name_or_a_downloaded_folder(tmp_path, capsys):
    """The launcher passes its pinned download's folder; anything else is refused at parse."""
    parser = build_parser()
    assert parser.parse_args(["--model", "large-v3-turbo"]).model == "large-v3-turbo"
    assert parser.parse_args(["--model", str(tmp_path)]).model == str(tmp_path)
    with pytest.raises(SystemExit):
        parser.parse_args(["--model", str(tmp_path / "missing")])
    assert "model folder" in capsys.readouterr().err
