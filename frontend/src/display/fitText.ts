/**
 * Height-aware font sizing for the audience display.
 *
 * The verse block must always fit the viewport: a projector screen has no
 * scrollbar, so overflowing text is simply unreadable. This module estimates
 * wrapped line counts from text metrics and shrinks the font until the block
 * fits. It is pure so it can be unit tested without a layout engine; the display
 * component supplies measured container dimensions.
 */

/** Font size bounds, in px. */
export const MIN_FONT_SIZE = 16
export const MAX_FONT_SIZE = 88

/**
 * Average glyph width as a fraction of font size. Measured across the serif
 * face used by the display; intentionally generous so the estimate errs toward
 * a smaller font rather than an overflow.
 */
const CHAR_WIDTH_RATIO = 0.5

/**
 * Vertical space taken by the reference line and container gaps, in px.
 *
 * Sized for a projector stage. A smaller consumer (the operator console's LIVE
 * preview) passes its own `chromePx` instead of re-deriving the estimate.
 */
const CHROME_PX = 160

export interface FitTextInput {
  /** Total characters to render across all lines. */
  charCount: number
  /** Number of separate blocks (one per verse). */
  lineCount: number
  /** Usable content width in px. */
  widthPx: number
  /** Usable content height in px. */
  heightPx: number
  /** CSS line-height multiplier applied to the verse text. */
  lineHeight: number
  /** Non-text space to leave free; defaults to the projector's allowance. */
  chromePx?: number
}

/**
 * Return the largest font size in px at which the text fits the given box.
 *
 * Binary search over [MIN_FONT_SIZE, MAX_FONT_SIZE]; returns MIN_FONT_SIZE when
 * even the smallest font would overflow, so callers always get a usable value.
 */
export function computeFitFontSize(input: FitTextInput): number {
  const { charCount, lineCount, widthPx, heightPx, lineHeight, chromePx = CHROME_PX } = input

  const blocks = Math.max(1, lineCount)
  const lines = Math.max(1, heightPx - chromePx)
  const width = Math.max(1, widthPx)

  const fits = (fontSize: number): boolean => {
    const charsPerLine = Math.max(1, Math.floor(width / (fontSize * CHAR_WIDTH_RATIO)))
    const totalLines = blocks * Math.ceil(charCount / charsPerLine)
    return totalLines * fontSize * lineHeight <= lines
  }

  // Text that fits at the largest size is the common case; skip the search.
  if (fits(MAX_FONT_SIZE)) return MAX_FONT_SIZE

  // Otherwise search downward for the largest size that fits.
  let low = MIN_FONT_SIZE
  let high = MAX_FONT_SIZE
  while (high - low > 0.5) {
    const mid = (low + high) / 2
    if (fits(mid)) low = mid
    else high = mid
  }
  // Clamp so text that overflows even at MIN still renders at a readable size.
  return Math.max(MIN_FONT_SIZE, Math.round(low))
}

/**
 * Refine an estimated font size by measuring the rendered layout.
 *
 * The estimator is deliberately conservative, so it can leave large gaps of
 * unused space; measuring reclaims that space while still guaranteeing no
 * overflow. Height does not scale linearly with font size (wrapping adds and
 * removes lines), so each candidate is measured rather than predicted.
 *
 * @param apply Applies a font size to the element being fitted.
 * @param measure Returns the height the text occupies at that size.
 * @param available Height in px the text may occupy.
 */
export function fitFontSizeByMeasurement(
  estimate: number,
  apply: (fontSize: number) => void,
  measure: () => number,
  available: number
): number {
  // Not laid out yet (hidden element or jsdom): keep the estimate.
  if (available <= 0) return estimate

  let size = estimate
  apply(size)

  // Too large: shrink until the text fits.
  while (size > MIN_FONT_SIZE && measure() > available) {
    size -= 1
    apply(size)
  }

  // Too small: grow while at least 8% of the available height is unused.
  // Re-measure each step because wrapping is not monotonic in font size.
  while (size < MAX_FONT_SIZE && measure() < available * 0.92) {
    const next = size + 1
    apply(next)
    if (measure() > available) {
      apply(size)
      break
    }
    size = next
  }

  return Math.min(MAX_FONT_SIZE, Math.max(MIN_FONT_SIZE, size))
}
