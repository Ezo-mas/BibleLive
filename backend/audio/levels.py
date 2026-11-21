"""Peak levels for 16 kHz mono, signed 16-bit little-endian PCM."""

import math
import sys
from array import array

BLOCK_SAMPLES = 800  # 50 ms at 16 kHz, matching frontend/src/shared/levels.ts.
BLOCK_BYTES = BLOCK_SAMPLES * 2


def block_level(block: bytes | bytearray) -> float:
    """Normalize a PCM block's peak to the -60..0 dBFS meter scale."""
    samples = memoryview(block).cast("h")
    if sys.byteorder == "big":
        native = array("h", samples)
        native.byteswap()
        samples = memoryview(native)
    peak = max(max(samples, default=0), -min(samples, default=0))
    if peak == 0:
        return 0.0
    dbfs = 20 * math.log10(peak / 32767)
    return max(0.0, min(1.0, (dbfs + 60) / 60))
