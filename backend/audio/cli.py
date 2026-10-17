"""Command line entry point for the BibleLive audio pipeline."""

import argparse
import logging
import time
from collections.abc import Callable
from typing import Any

from core.config import config as server_config

from .capture import list_input_devices
from .config import AudioPipelineConfig
from .logging_config import configure_logging
from .pipeline import BibleLiveAudioProcessor
from .stt import SUPPORTED_LANGUAGES
from .vad import VAD_ENGINES

logger = logging.getLogger(__name__)


_TUNING_DEFAULTS = AudioPipelineConfig()


def _flag(value: str) -> bool:
    """Parse an on/off tuning value (1/0, true/false, yes/no, on/off)."""
    lowered = value.strip().lower()
    if lowered in ("1", "true", "yes", "on"):
        return True
    if lowered in ("0", "false", "no", "off"):
        return False
    raise argparse.ArgumentTypeError(f"expected on/off, got {value!r}")


def _vad_engine(value: str) -> str:
    """Parse a VAD engine name."""
    if value not in VAD_ENGINES:
        raise argparse.ArgumentTypeError(f"expected one of {', '.join(VAD_ENGINES)}, got {value!r}")
    return value


# (flag, config field, type, help) — shared with benchmarks/run_benchmark.py so
# every live tuning knob can be swept there under the same name.
TUNING_ARGUMENTS: list[tuple[str, str, Callable[[str], Any], str]] = [
    ("--beam-size", "beam_size", int, "Whisper beam width; lower is faster"),
    ("--cpu-threads", "cpu_threads", int, "Whisper CPU threads (0 = library default)"),
    ("--end-of-speech-s", "end_of_speech_s", float, "silence that ends an utterance"),
    ("--min-speech-s", "min_speech_s", float, "shorter utterances are dropped"),
    ("--soft-utterance-s", "soft_utterance_s", float, "past this, end at the next pause"),
    ("--max-utterance-s", "max_utterance_s", float, "hard utterance length cap"),
    ("--pre-roll-s", "pre_roll_s", float, "audio kept from before speech starts"),
    ("--post-roll-s", "post_roll_s", float, "silence kept after speech ends"),
    ("--keep-pauses", "keep_pauses", _flag, "keep in-utterance frames the VAD calls silent"),
    ("--start-voiced-frames", "start_voiced_frames", int, "voiced frames needed to start"),
    ("--start-window-frames", "start_window_frames", int, "...within this many recent frames"),
    ("--vad", "vad", _vad_engine, "VAD engine: webrtc or silero"),
    ("--silero-threshold", "silero_threshold", float, "Silero speech probability threshold"),
    (
        "--silero-neg-threshold",
        "silero_neg_threshold",
        float,
        "Silero: speech lasts until the probability drops below this (None = threshold - 0.15)",
    ),
]


def add_tuning_arguments(parser: argparse.ArgumentParser) -> None:
    """Add the speech-tuning flags; each defaults to None (= config default)."""
    for flag, field_name, kind, help_text in TUNING_ARGUMENTS:
        default = getattr(_TUNING_DEFAULTS, field_name)
        parser.add_argument(flag, type=kind, default=None, help=f"{help_text} (default: {default})")


def apply_tuning_arguments(config: AudioPipelineConfig, args: argparse.Namespace) -> None:
    """Copy any tuning flags that were given onto ``config``."""
    for _name, field_name, _kind, _help in TUNING_ARGUMENTS:
        value = getattr(args, field_name)
        if value is not None:
            setattr(config, field_name, value)


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(description="BibleLive Audio Processing Pipeline")
    parser.add_argument(
        "--bible-db", default="bible_data/biblelive.sqlite", help="Path to Bible SQLite database"
    )
    parser.add_argument(
        "--verse-text-db",
        default="bible_data/bible_ltb.sqlite",
        help=(
            "Lithuanian Bible SQLite database: when a Lithuanian speaker names only a "
            "chapter and reads a verse aloud, report that verse (skipped if missing)"
        ),
    )
    parser.add_argument("--server", default="http://localhost:8000", help="BibleLive server URL")
    parser.add_argument(
        "--server-token",
        default=None,
        help=(
            "Operator token sent with each reference; needed when the server requires "
            "auth. Default: BIBLELIVE_OPERATOR_TOKEN from the environment or .env "
            "(prefer that: a flag value is visible in the process list)"
        ),
    )
    parser.add_argument(
        "--model",
        default="small",
        choices=[
            "tiny",
            "base",
            "small",
            "medium",
            "large-v2",
            "large-v3",
            "large-v3-turbo",
            "distil-large-v3",
        ],
        help=(
            "Whisper model size (default: small). On a GPU, large-v3-turbo with "
            "--compute-type float16 is far more accurate for Lithuanian at similar speed; "
            "distil-* models are English-only"
        ),
    )
    parser.add_argument(
        "--device", default="cuda", choices=["cpu", "cuda"], help="Device for Whisper model"
    )
    parser.add_argument(
        "--compute-type",
        default=None,
        choices=["float16", "float32", "int8", "int8_float16"],
        help="Whisper compute type (default: int8; use float16 on RTX 20xx+ GPUs)",
    )
    parser.add_argument(
        "--language",
        default="en",
        choices=list(SUPPORTED_LANGUAGES),
        help="Spoken language: en=English, lt=Lithuanian",
    )
    parser.add_argument(
        "--vad-aggressiveness",
        type=int,
        default=2,
        choices=[0, 1, 2, 3],
        help="WebRTC VAD aggressiveness (0=least, 3=most aggressive)",
    )
    add_tuning_arguments(parser)
    parser.add_argument(
        "--input-device",
        default=None,
        help=(
            "Microphone to capture: the exact DirectShow name on Windows (see "
            "--list-input-devices), a PulseAudio source on Linux, an avfoundation index "
            "on macOS. Default: the first Windows input, or the system default elsewhere."
        ),
    )
    parser.add_argument(
        "--list-input-devices",
        action="store_true",
        help="Print the audio inputs ffmpeg can open (Windows) and exit",
    )
    parser.add_argument(
        "--control-host",
        default="127.0.0.1",
        help="Host for the runtime language-control server (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--control-port",
        type=int,
        default=8001,
        help="Port for the runtime language-control server (default: 8001)",
    )
    parser.add_argument(
        "--control-token",
        default=None,
        help="Bearer token the control server requires when set (recommended off-loopback)",
    )
    parser.add_argument(
        "--no-control",
        action="store_true",
        help="Disable the runtime language-control server",
    )
    parser.add_argument(
        "--log-level",
        default=None,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help=(
            "Pipeline log level (default: INFO, or $BIBLELIVE_AUDIO_LOG_LEVEL). "
            "DEBUG also traces ffmpeg output and audio frame accounting."
        ),
    )
    return parser


def main() -> None:
    """Main function to run the BibleLive audio pipeline"""
    args = build_parser().parse_args()

    if args.list_input_devices:
        devices = list_input_devices()
        if devices:
            print("Audio inputs (pass one to --input-device, quoted):")
            for name in devices:
                print(f"  {name}")
        else:
            print(
                "No DirectShow audio inputs found. Listing is Windows-only; on Linux use "
                "`pactl list short sources`, on macOS "
                '`ffmpeg -f avfoundation -list_devices true -i ""`.'
            )
        return

    # Configure logging before any component starts so audio capture, VAD,
    # transcription and reference sending all report progress.
    level = configure_logging(args.log_level)
    logger.info("Logging configured at %s level", logging.getLevelName(level))

    # Build config from CLI args and create audio processor
    config = AudioPipelineConfig(
        whisper_model=args.model,
        whisper_device=args.device,
        whisper_compute_type=args.compute_type,
        language=args.language,
        vad_aggressiveness=args.vad_aggressiveness,
        input_device=args.input_device,
        control_enabled=not args.no_control,
        control_host=args.control_host,
        control_port=args.control_port,
        control_token=args.control_token,
        # Same source the server reads its token from, so one .env covers both.
        api_token=args.server_token or server_config.OPERATOR_TOKEN,
    )
    apply_tuning_arguments(config, args)
    processor = BibleLiveAudioProcessor(
        bible_db_path=args.bible_db,
        server_url=args.server,
        config=config,
        verse_text_db_path=args.verse_text_db,
    )

    try:
        processor.start()

        # Keep running until interrupted
        print("\nBibleLive Audio Pipeline is running...")
        print("Speak into your microphone to detect Bible references")
        if config.control_enabled:
            print(
                f"Language control: GET/POST http://{config.control_host}:{config.control_port}"
                "/language"
            )
        print("Press Ctrl+C to stop\n")

        while processor.is_running:
            time.sleep(1)

    except KeyboardInterrupt:
        print("\n\nStopping audio pipeline...")
    except Exception as e:
        logger.error(f"Error in audio pipeline: {e}")
    finally:
        processor.stop()


if __name__ == "__main__":
    main()
