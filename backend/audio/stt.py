"""Speech-to-text transcription using faster-whisper."""

import logging
import re
import time

import numpy as np
from faster_whisper import WhisperModel

logger = logging.getLogger(__name__)

# Per-language Whisper initial prompts: bias the decoder toward Bible
# vocabulary and "chapter/verse" number patterns for each supported language.
WHISPER_INITIAL_PROMPTS: dict[str, str] = {
    "en": (
        "The sermon references Bible books such as Genesis, Exodus, Leviticus, "
        "Numbers, Deuteronomy, Joshua, Judges, Ruth, Samuel, Kings, Chronicles, "
        "Ezra, Nehemiah, Esther, Job, Psalms, Proverbs, Ecclesiastes, Isaiah, "
        "Jeremiah, Ezekiel, Daniel, Matthew, Mark, Luke, John, Acts, Romans, "
        "Corinthians, Galatians, Ephesians, Philippians, Colossians, Thessalonians, "
        "Timothy, Titus, Philemon, Hebrews, James, Peter, Jude, and Revelation. "
        "For example: turn to John chapter three verse sixteen, or Romans 8:28."
    ),
    "lt": (
        "Pamokslas mini Rašto knygas: Pradžios, Išėjimo, Kunigų, Skaičių, "
        "Pakartoto Įstatymo, Jozuės, Teisėjų, Rūtos, Samuelio, Karalių, "
        "Metraščių, Ezros, Nehemijo, Esteros, Jobo, Psalmynas, Psalmės, "
        "Patarlių, Mokytojo, Izaijo, Jeremijo, Ezechielio, Danielės, Mato, "
        "Morkaus, Luko, Jono evangelija, Apaštalų darbai, Romiečiams, "
        "Korintiečiams, Galatams, Efeziečiams, Filipiečiams, Kolosiečiams, "
        "Tesalonikiečiams, Timotėjui, Titui, Filemonui, Hebrėjams, Jokūbo, "
        "Petro, Judo, Apreiškimas Jonui. Pavyzdžiui: Jono evangelija "
        "trečias skyrius, šešiolikta eilutė."
    ),
}

SUPPORTED_LANGUAGES = tuple(WHISPER_INITIAL_PROMPTS.keys())

# Regex corrections for merged "book chapter verse" numbers.
_REFERENCE_CORRECTIONS: dict[str, str] = {
    # "mark112" -> "mark 1 12"
    r"\b(john|jude|genesis|exodus|leviticus|numbers|deuteronomy|joshua|judges|ruth|1\s?samuel|2\s?samuel|1\s?kings|2\s?kings|1\s?chronicles|2\s?chronicles|ezra|nehemiah|esther|job|psalms|proverbs|ecclesiastes|song\s?of\s?solomon|isaiah|jeremiah|lamentations|ezekiel|daniel|hosea|joel|amos|obadiah|jonah|micah|nahum|habakkuk|zephaniah|haggai|zechariah|malachi|matthew|mark|luke|john|acts|romans|1\s?corinthians|2\s?corinthians|galatians|ephesians|philippians|colossians|1\s?thessalonians|2\s?thessalonians|1\s?timon|2\s?timon|titus|philemon|hebrews|james|1\s?peter|2\s?peter|1\s?john|2\s?john|3\s?john|jude|revelation)\s*(\d+)\s*(\d+)\b": r"\1 \2:\3",
    # "mark 1:12 5" -> "mark 1:12-5"
    r"\b(john|jude|genesis|exodus|leviticus|numbers|deuteronomy|joshua|judges|ruth|1\s?samuel|2\s?samuel|1\s?kings|2\s?kings|1\s?chronicles|2\s?chronicles|ezra|nehemiah|esther|job|psalms|proverbs|ecclesiastes|song\s?of\s?solomon|isaiah|jeremiah|lamentations|ezekiel|daniel|hosea|joel|amos|obadiah|jonah|micah|nahum|habakkuk|zephaniah|haggai|zechariah|malachi|matthew|mark|luke|john|acts|romans|1\s?corinthians|2\s?corinthians|galatians|ephesians|philippians|colossians|1\s?thessalonians|2\s?thessalonians|1\s?timon|2\s?timon|titus|philemon|hebrews|james|1\s?peter|2\s?peter|1\s?john|2\s?john|3\s?john|jude|revelation)\s*(\d+):(\d+)\s*(\d+)\b": r"\1 \2:\3-\4",
}

# Whisper hallucination guard. On low-energy or tonal input the decoder can
# latch onto a phrase from the initial prompt and loop it for the rest of the
# buffer ("Romans 8, or Romans 8, or Romans 8, ..." for tens of seconds). A
# sermon transcript never repeats a phrase this many times back to back, so
# anything at or above these thresholds is treated as a decoding artifact.
MIN_REPEAT_BLOCK_WORDS = 2  # shortest phrase that can be the repeated block
MIN_REPEAT_COUNT = 3  # repetitions of that block before we call it a loop
MIN_REPEAT_WORDS = 8  # total repeated words before we call it a loop


class SpeechToText:
    """Handles speech-to-text transcription using faster-whisper"""

    def __init__(
        self,
        model_size: str = "small",
        device: str = "cuda",
        language: str = "en",
        compute_type: str | None = None,
    ):
        if language not in SUPPORTED_LANGUAGES:
            raise ValueError(
                f"Unsupported language '{language}'. Supported: {', '.join(SUPPORTED_LANGUAGES)}"
            )
        self.language = language
        self.initial_prompt = WHISPER_INITIAL_PROMPTS[language]

        # Default compute type: int8 (works on all GPUs incl. Pascal GTX 10xx
        # and CPU). Pass compute_type explicitly (e.g. "float16") on newer GPUs
        # (Turing RTX 20xx+ / Ampere+) for best quality.
        if compute_type is None:
            compute_type = "int8"
        elif device == "cuda":
            import ctranslate2

            supported = ctranslate2.get_supported_compute_types("cuda")
            if compute_type not in supported:
                logger.warning(
                    f"compute_type '{compute_type}' not supported on this GPU "
                    f"(supported: {sorted(supported)}), falling back to 'int8'"
                )
                compute_type = "int8"

        logger.info(
            f"Loading Whisper model '{model_size}' on {device} "
            f"(compute_type={compute_type}, language={language})..."
        )
        try:
            self.model = WhisperModel(model_size, device=device, compute_type=compute_type)
            logger.info("Whisper model loaded successfully")
        except Exception as e:
            logger.error(f"Failed to load Whisper model: {e}")
            raise

        # Audio buffer for accumulating speech
        self.audio_buffer: list[float] = []
        self.sample_rate = 16000

    def add_audio_frame(self, frame: bytes):
        """Add audio frame to buffer for transcription"""
        # Convert bytes to numpy array
        audio_np = np.frombuffer(frame, dtype=np.int16).astype(np.float32) / 32768.0
        self.audio_buffer.extend(audio_np)

    def transcribe_buffer(self) -> tuple[str | None, float]:
        """
        Transcribe accumulated audio buffer and clear it.

        Returns:
            Tuple of (transcribed_text, duration_ms)
        """
        if not self.audio_buffer:
            return None, 0.0

        # Convert buffer to numpy array
        audio_np = np.array(self.audio_buffer, dtype=np.float32)

        # Clear buffer for next utterance
        self.audio_buffer = []

        # Skip if audio is too short (less than 0.5 seconds)
        if len(audio_np) < self.sample_rate * 0.5:
            return None, 0.0

        start_time = time.time()

        try:
            # Transcribe with Whisper
            segments, info = self.model.transcribe(
                audio_np,
                language=self.language,
                initial_prompt=self.initial_prompt,
                beam_size=5,
                best_of=5,
                temperature=0.0,
                compression_ratio_threshold=2.4,
                log_prob_threshold=-1.0,
                no_speech_threshold=0.6,
                condition_on_previous_text=False,
                # Decoder-side loop guards (post-processing in
                # _collapse_repetition is the backstop if these are ever hit).
                repetition_penalty=1.05,
                no_repeat_ngram_size=6,
            )

            # Combine all segments
            text = " ".join([segment.text.strip() for segment in segments]).strip()

            # Apply post-processing fixes for common transcription errors
            text = self._fix_common_transcription_errors(text)

            # Drop degenerate decoder loops before anything downstream sees them
            text, loop = self._collapse_repetition(text)

            duration_ms = (time.time() - start_time) * 1000

            if loop:
                logger.warning(f"Discarded Whisper repetition loop ({loop})")

            return text if text else None, duration_ms

        except RuntimeError as e:
            duration_ms = (time.time() - start_time) * 1000
            logger.error(f"Whisper runtime error: {e}")
            return None, duration_ms
        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            logger.error(f"Unexpected transcription error: {e}", exc_info=True)
            return None, duration_ms

    def _collapse_repetition(self, text: str) -> tuple[str, str | None]:
        """Collapse a degenerate Whisper repetition loop.

        Whisper sometimes latches onto a phrase (often one seeded by the initial
        prompt) and repeats it for the rest of the buffer. That single artifact
        turns into dozens of "found reference" lines downstream, so the repeated
        block is reduced to one occurrence here.

        Returns the cleaned text and a description of the loop that was removed,
        or ``None`` when the text is not looping.
        """
        words = text.split()
        if len(words) < MIN_REPEAT_COUNT * MIN_REPEAT_BLOCK_WORDS:
            return text, None

        # Try each block size, and each start position, looking for a block that
        # repeats back to back often enough to be a decoding artifact.
        best: tuple[int, int, int] | None = None  # (start, block_len, count)
        max_block = max(MIN_REPEAT_BLOCK_WORDS, len(words) // MIN_REPEAT_COUNT)

        for block_len in range(MIN_REPEAT_BLOCK_WORDS, max_block + 1):
            for start in range(0, len(words) - block_len * MIN_REPEAT_COUNT + 1):
                block = words[start : start + block_len]
                count = 1
                while words[start + block_len * count : start + block_len * (count + 1)] == block:
                    count += 1
                if count < MIN_REPEAT_COUNT or count * block_len < MIN_REPEAT_WORDS:
                    continue
                # Prefer the earliest, longest match.
                if best is None or start < best[0]:
                    best = (start, block_len, count)

        if best is None:
            return text, None

        start, block_len, count = best
        collapsed = words[: start + block_len]  # keep one occurrence of the block
        block_text = " ".join(words[start : start + block_len])
        return " ".join(collapsed), f"'{block_text}' repeated {count} times, trimmed to 1"

    def _fix_common_transcription_errors(self, text: str) -> str:
        """Fix common transcription errors in Bible references"""
        # More conservative approach: only rewrite clearly merged
        # "book<digits><digits>" / "book<c:v> <v>" patterns, and only when the
        # surrounding words look like a Bible reference.
        for pattern, replacement in _REFERENCE_CORRECTIONS.items():
            text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)

        return text
