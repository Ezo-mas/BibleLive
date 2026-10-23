import { useLayoutEffect } from 'react'
import type { RefObject } from 'react'
import {
  MAX_FONT_SIZE,
  MIN_FONT_SIZE,
  computeFitFontSize,
  fitFontSizeByMeasurement,
} from '../../display/fitText'

/** Line height of the verse text, the same as the audience display's. */
const PREVIEW_LINE_HEIGHT = 1.5
/** The audience display is laid out for this width; the preview scales its bounds from it. */
const DISPLAY_REFERENCE_WIDTH_PX = 1920

/**
 * Size the verse text in the display preview the way the audience display sizes it.
 *
 * The display shrinks the font until the visible verses fit the screen; the preview
 * does the same with the same logic (`fitText.ts`) against its own box, so a long
 * passage (ten verses, or all of them) shrinks to fit here exactly as it does on the
 * projector instead of running out of the box. The size bounds are the display's,
 * scaled by how much smaller the preview is than the display's reference screen.
 *
 * @param block The box the verses must fit: it fills the preview above the citation.
 * @param inner The verses' own wrapper, whose natural height is the overflow signal.
 * @param passageKey Changes with the passage and the verse limit, so a new passage is refitted.
 */
export function usePreviewFit(
  monitor: RefObject<HTMLElement>,
  block: RefObject<HTMLElement>,
  inner: RefObject<HTMLElement>,
  charCount: number,
  lineCount: number,
  passageKey: string
): void {
  useLayoutEffect(() => {
    const monitorEl = monitor.current
    const blockEl = block.current
    const innerEl = inner.current
    if (!monitorEl || !blockEl || !innerEl) return

    const fit = () => {
      const available = blockEl.clientHeight
      const width = blockEl.clientWidth
      // Not laid out (hidden, or jsdom): leave the stylesheet's size alone.
      if (available <= 0 || width <= 0) return
      const scale = monitorEl.clientWidth / DISPLAY_REFERENCE_WIDTH_PX
      const bounds = { min: MIN_FONT_SIZE * scale, max: MAX_FONT_SIZE * scale }
      const estimate = computeFitFontSize({
        charCount,
        lineCount,
        widthPx: width,
        heightPx: available,
        lineHeight: PREVIEW_LINE_HEIGHT,
        chromePx: 0,
        minFontSize: bounds.min,
        maxFontSize: bounds.max,
      })
      const size = fitFontSizeByMeasurement(
        estimate,
        next => {
          blockEl.style.fontSize = `${next}px`
        },
        () => innerEl.offsetHeight,
        available,
        bounds
      )
      blockEl.style.fontSize = `${size}px`
    }

    fit()
    if (typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(fit)
    observer.observe(blockEl)
    return () => observer.disconnect()
  }, [monitor, block, inner, charCount, lineCount, passageKey])
}
