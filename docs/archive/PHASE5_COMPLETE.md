# Phase 5: Audio Pipeline Reliability

## Summary

Hardened the audio processing pipeline with proper error handling, bounded queues, retry logic, deduplication, metrics tracking, and explicit worker lifecycle management.

## Key Changes

### 1. Fixed ffmpeg stderr Blocking Issue

**Problem:** ffmpeg stderr was captured but not consumed, causing pipe buffer to fill and block.

**Solution:**
- Added dedicated stderr consumer thread
- Logs ffmpeg output at debug level
- Prevents blocking and allows ffmpeg to run indefinitely

### 2. Bounded Queues with Backpressure Handling

**Problem:** Unbounded queues could consume unlimited memory if processing fell behind.

**Solution:**
- Audio queue: bounded to 1000 chunks (~10 seconds)
- VAD queue: bounded to 100 speech segments
- Graceful backpressure: drops audio/segments with warning if queues fill
- Configurable limits via `AudioPipelineConfig`

### 3. Replaced Broad Exception Handlers

**Problem:** Bare `except:` and `except Exception` blocks hid important failures.

**Solution:**
- AudioCapture: Specific handlers for `OSError`, `FileNotFoundError`, `subprocess.SubprocessError`
- VAD: Specific handler for `webrtcvad.Error`
- SpeechToText: Specific handlers for `RuntimeError` (Whisper errors)
- API calls: Specific handlers for `Timeout`, `ConnectionError`, `RequestException`
- All unexpected exceptions logged with `exc_info=True` for stack traces

### 4. Retry Logic with Exponential Backoff

**Problem:** API delivery failures had no retry mechanism.

**Solution:**
- Configurable retry attempts (default: 3)
- Exponential backoff: base 0.5s, max 5s
- Jitter added to prevent thundering herd
- Retries only for 5xx server errors and timeouts
- 4xx client errors fail immediately (no retry)
- Metrics track retry attempts

### 5. Reference Deduplication

**Problem:** Same reference detected repeatedly flooded pending queue.

**Solution:**
- Hash-based deduplication using MD5 of `book:chapter:verse_start-verse_end`
- Configurable window (default: 5 seconds)
- Automatic cleanup of expired entries
- Metrics track deduplicated references

### 6. Comprehensive Metrics and Observability

**Added PipelineMetrics class tracking:**
- Audio chunks captured
- VAD segments detected
- Transcription attempts/successes/failures
- Transcription duration (moving average of last 100)
- References detected/sent/failed/deduplicated
- API retry count
- API request duration (moving average)
- Queue depths (audio and VAD)

**Metrics logging:**
- Automatic summary every 60 seconds (configurable)
- Final summary on shutdown
- All metrics accessible for external monitoring

### 7. Explicit Worker Lifecycle Management

**Old design:** Single processing loop doing VAD + transcription serially

**New design:**
- Separate VAD thread: detects speech segments, feeds VAD queue
- Separate transcription thread: processes segments from queue
- Better parallelism: VAD continues while transcription runs
- Proper thread naming for debugging
- Graceful shutdown with thread joins and timeouts
- Resource cleanup: threads, processes, database connections

### 8. Configuration Management

**Added AudioPipelineConfig dataclass:**
```python
audio_queue_maxsize: int = 1000
vad_queue_maxsize: int = 100
api_max_retries: int = 3
api_retry_base_delay: float = 0.5
api_retry_max_delay: float = 5.0
api_timeout: float = 5.0
dedup_window_seconds: float = 5.0
metrics_log_interval: int = 60
```

### 9. Better Error Messages

- ffmpeg errors: specific "not found" vs "failed to start"
- Database errors with thread name
- Transcription timing logged
- Queue full warnings identify which queue
- Connection errors show retry attempt
- Unexpected errors include stack traces

### 10. Removed Emojis


## Files Modified

**Modified:**
- `audio/biblelive_audio_pipeline.py` (809 lines)
  - Added metrics and config classes (124 lines)
  - Rewrote AudioCapture with stderr handling and bounded queues
  - Improved exception specificity in VAD and SpeechToText
  - Split processor into VAD and transcription threads
  - Added retry, deduplication, and metrics to reference sending
  - Updated main() to remove emojis

## Usage

### Basic Usage (unchanged)

```bash
python -m audio.biblelive_audio_pipeline
```

### With Custom Configuration

```python
from audio.biblelive_audio_pipeline import (
    BibleLiveAudioProcessor,
    AudioPipelineConfig
)

config = AudioPipelineConfig(
    audio_queue_maxsize=2000,
    api_max_retries=5,
    dedup_window_seconds=10.0,
    metrics_log_interval=30
)

processor = BibleLiveAudioProcessor(
    bible_db_path="bible_data/biblelive.sqlite",
    server_url="http://localhost:8000",
    config=config
)
processor.start()
```

### Monitoring Metrics

Metrics logged automatically every 60 seconds:

```
=== Pipeline Metrics ===
  Audio chunks: 12450
  VAD segments: 23
  Transcriptions: 23/23 (0 failed)
  Avg transcription time: 342.5ms
  References detected: 15
  References sent: 12 (1 failed, 2 deduplicated)
  API retries: 3
  Avg API request time: 45.2ms
  Queue depths - Audio: 45, VAD: 1
```

## Benefits

### Reliability
- No more ffmpeg blocking
- Memory-bounded even under load
- Graceful degradation with backpressure
- Automatic retry for transient failures

### Observability
- Clear metrics for all pipeline stages
- Specific error messages with context
- Stack traces for debugging
- Queue depth visibility

### Performance
- Parallel VAD and transcription
- Non-blocking pipeline operation
- Deduplication reduces API load

### Production-Ready
- Configurable for different environments
- Explicit resource cleanup
- No silent failures
- Professional logging

## Breaking Changes

**None for basic usage.** However:

1. **BibleLookup:** Now uses `readonly=True` - ensure database exists before starting
2. **Config parameter:** New optional `config` parameter (defaults provided)
3. **Transcribe return:** Now returns `Tuple[Optional[str], float]` with duration

## Testing Performed

✓ File compiles successfully  
✓ All imports resolve  
✓ Syntax validated  

**Manual testing recommended:**
- Run with real microphone
- Verify metrics logging
- Test retry (stop server temporarily)
- Test deduplication (repeat references)
- Test backpressure (slow API)
- Verify graceful shutdown

## Next: Phase 6 - Test/Tooling/CI Foundation

Replaced emoji indicators with plain text for professional production logs.
