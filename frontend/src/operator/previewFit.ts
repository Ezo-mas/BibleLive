import { computeFitFontSize } from '../display/fitText'

/** Line-height multiplier of the verse text in the display preview. */
const PREVIEW_LINE_HEIGHT = 1.5

/**
 * Nominal size of the display preview, in px. The preview is a 16:9 box that
 * scales with the window and sizes its text in container-query units, so the fit
 * estimate is made once against this reference size and converted to a fraction
 * of the preview's width. Whatever size the box ends up, the passage fills the
 * same share of it as it would at the reference size.
 */
const PREVIEW_WIDTH_PX = 800
const PREVIEW_HEIGHT_PX = 450
/** The preview's own padding (8% sides, 5% top and bottom) leaves this much to the text. */
const PREVIEW_CONTENT_WIDTH_RATIO = 0.84
const PREVIEW_CONTENT_HEIGHT_RATIO = 0.9
/** Reference line and gaps inside the preview, at the reference size. */
const PREVIEW_CHROME_PX = 80
/** Readability bounds at the reference size, in px. */
const PREVIEW_MIN_FONT_PX = 14
const PREVIEW_MAX_FONT_PX = 30

/**
 * Verse text size for the display preview, in `cqw` (percent of the preview's
 * width), so it scales with the box.
 */
export function previewFontCqw(verses: readonly { text: string }[]): number {
  const px = computeFitFontSize({
    charCount: verses.reduce((sum, verse) => sum + verse.text.length, 0),
    // The estimate counts every line as one block of the total text; the gaps between
    // verses are inside the chrome allowance.
    lineCount: 1,
    widthPx: PREVIEW_WIDTH_PX * PREVIEW_CONTENT_WIDTH_RATIO,
    heightPx: PREVIEW_HEIGHT_PX * PREVIEW_CONTENT_HEIGHT_RATIO,
    lineHeight: PREVIEW_LINE_HEIGHT,
    chromePx: PREVIEW_CHROME_PX,
    minFontSize: PREVIEW_MIN_FONT_PX,
    maxFontSize: PREVIEW_MAX_FONT_PX,
  })
  return (px / PREVIEW_WIDTH_PX) * 100
}
