"""Command line entry point for the BibleLive audio pipeline."""

import argparse
import logging
import time

from .config import AudioPipelineConfig
from .logging_config import configure_logging
from .pipeline import BibleLiveAudioProcessor
from .stt import SUPPORTED_LANGUAGES

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(description="BibleLive Audio Processing Pipeline")
    parser.add_argument(
        "--bible-db", default="bible_data/biblelive.sqlite", help="Path to Bible SQLite database"
    )
    parser.add_argument("--server", default="http://localhost:8000", help="BibleLive server URL")
    parser.add_argument(
        "--model",
        default="small",
        choices=["tiny", "base", "small", "medium", "large-v2", "large-v3", "distil-large-v3"],
        help="Whisper model size (default: small)",
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
    )
    processor = BibleLiveAudioProcessor(
        bible_db_path=args.bible_db, server_url=args.server, config=config
    )

    try:
        processor.start()

        # Keep running until interrupted
        print("\nBibleLive Audio Pipeline is running...")
        print("Speak into your microphone to detect Bible references")
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
