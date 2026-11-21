/**
 * Options and fitting for the stream overlay: `/display?mode=obs`, an OBS
 * "Browser" source that shows the passage on screen as a lower third over a
 * transparent page instead of the projector's full-screen slide.
 *
 * The URL carries the overlay's own layout; what is shown (the passage, the
 * verse limit) is the projector's, pushed by the server as usual.
 */

export type BandPosition = 'bottom' | 'top'
export type BandStyle = 'band' | 'none'

export interface StreamOverlayOptions {
  /** Band height as a percentage of the frame. */
  heightPct: number
  position: BandPosition
  /** 'band': text on a dark translucent band; 'none': text with a shadow only. */
  style: BandStyle
}

export const DEFAULT_HEIGHT_PCT = 30
const MIN_HEIGHT_PCT = 15
const MAX_HEIGHT_PCT = 60

/** True when the display page should render the stream overlay. */
export function isStreamOverlay(search: string): boolean {
  return new URLSearchParams(search).get('mode') === 'obs'
}

/** Overlay options from the page URL; anything missing or invalid uses the default. */
export function parseStreamOverlayOptions(search: string): StreamOverlayOptions {
  const params = new URLSearchParams(search)
  const height = Number(params.get('height'))
  return {
    heightPct:
      Number.isFinite(height) && height > 0
        ? Math.min(MAX_HEIGHT_PCT, Math.max(MIN_HEIGHT_PCT, height))
        : DEFAULT_HEIGHT_PCT,
    position: params.get('position') === 'top' ? 'top' : 'bottom',
    style: params.get('bg') === 'none' ? 'none' : 'band',
  }
}

/**
 * Text size bounds for a stream frame of `frameHeightPx`, in px.
 *
 * The minimum is what still reads on a phone watching the stream (about 28 px at
 * 1080p); the projector's 16 px floor would be unreadable there.
 */
export function streamFontBounds(frameHeightPx: number): { min: number; max: number } {
  return {
    min: Math.max(14, Math.round(frameHeightPx * 0.026)),
    max: Math.max(20, Math.round(frameHeightPx * 0.06)),
  }
}

export interface BandFit {
  /** Verses shown, counted from the first. */
  count: number
  fontSize: number
}

/**
 * Show as many of the first `total` verses as fit the band.
 *
 * `fitAt(count)` lays out the first `count` verses, fits the font within the
 * stream bounds, and says whether the text fits at that size. A passage that is
 * too long even at the smallest readable size loses verses from its end (the
 * overlay then notes how many are left out); a single verse is always shown.
 */
export function fitVersesToBand(
  total: number,
  fitAt: (count: number) => { fontSize: number; fits: boolean }
): BandFit {
  for (let count = total; count > 1; count--) {
    const { fontSize, fits } = fitAt(count)
    if (fits) return { count, fontSize }
  }
  return { count: Math.min(total, 1), fontSize: fitAt(Math.min(total, 1)).fontSize }
}
