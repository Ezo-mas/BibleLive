"""Tests for the audio pipeline CLI arguments."""

import pytest

from audio.cli import build_parser

# Requires the optional audio extras (faster-whisper).
pytestmark = pytest.mark.audio


def test_every_model_choice_is_a_name_faster_whisper_knows():
    """A choice the installed faster-whisper cannot resolve fails only at load."""
    from faster_whisper.utils import available_models

    model_action = next(a for a in build_parser()._actions if a.dest == "model")

    assert set(model_action.choices) <= set(available_models())
    assert "large-v3-turbo" in model_action.choices
