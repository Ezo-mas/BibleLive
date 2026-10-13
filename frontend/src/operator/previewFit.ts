import { computeFitFontSize } from '../display/fitText'

/** Line-height multiplier of the verse text on the console's stages. */
const PREVIEW_LINE_HEIGHT = 1.5

/**
 * Nominal size of a console stage, in px. The stage is a flexible box in CSS (it
 * grows with the passage and scrolls past about half the window); the fit
 * estimate is only a guide to a readable size, and it assumes the stage's
 * floor so growth only ever adds room.
 */
const PREVIEW_WIDTH_PX = 440
const PREVIEW_HEIGHT_PX = 300
/** Reference line and gaps inside a stage. */
const PREVIEW_CHROME_PX = 60
/** Readability bounds for the miniature; the projector itself allows 16–88. */
const PREVIEW_MIN_FONT_PX = 14
const PREVIEW_MAX_FONT_PX = 20

/**
 * Font size for a passage on the LIVE or UP NEXT stage.
 *
 * Both stages call this, so the same passage is the same size whichever side of
 * the console it is on — the operator compares the two by eye.
 */
export function previewFontSize(verses: readonly { text: string }[]): number {
  return Math.min(
    PREVIEW_MAX_FONT_PX,
    Math.max(
      PREVIEW_MIN_FONT_PX,
      computeFitFontSize({
        charCount: verses.reduce((sum, verse) => sum + verse.text.length, 0),
        lineCount: verses.length,
        widthPx: PREVIEW_WIDTH_PX,
        heightPx: PREVIEW_HEIGHT_PX + PREVIEW_CHROME_PX,
        lineHeight: PREVIEW_LINE_HEIGHT,
        chromePx: PREVIEW_CHROME_PX,
      })
    )
  )
}
