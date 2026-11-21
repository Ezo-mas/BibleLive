import { describe, expect, it } from 'vitest'
import {
  DEFAULT_HEIGHT_PCT,
  DEFAULT_STREAM_OVERLAY_OPTIONS,
  fitVersesToBand,
  isStreamOverlay,
  isStreamOverlayPreview,
  parseStreamOverlayOptions,
  streamFontBounds,
  streamOverlayQuery,
} from './streamOverlayOptions'

describe('stream overlay options', () => {
  it('is on only for mode=obs', () => {
    expect(isStreamOverlay('?mode=obs')).toBe(true)
    expect(isStreamOverlay('?mode=obs&height=40')).toBe(true)
    expect(isStreamOverlay('')).toBe(false)
    expect(isStreamOverlay('?mode=projector')).toBe(false)
  })

  it('defaults to a bottom band of 30% of the frame', () => {
    expect(parseStreamOverlayOptions('?mode=obs')).toEqual({
      heightPct: DEFAULT_HEIGHT_PCT,
      position: 'bottom',
      style: 'band',
      font: 'serif',
      align: 'left',
    })
  })

  it('reads height, position and background from the URL', () => {
    expect(parseStreamOverlayOptions('?mode=obs&height=40&position=top&bg=none')).toEqual({
      heightPct: 40,
      position: 'top',
      style: 'none',
      font: 'serif',
      align: 'left',
    })
  })

  it('keeps the height within 15-60% and ignores garbage', () => {
    expect(parseStreamOverlayOptions('?height=5').heightPct).toBe(15)
    expect(parseStreamOverlayOptions('?height=90').heightPct).toBe(60)
    expect(parseStreamOverlayOptions('?height=abc').heightPct).toBe(DEFAULT_HEIGHT_PCT)
    expect(parseStreamOverlayOptions('?position=left').position).toBe('bottom')
  })

  it('reads the font and alignment, defaulting to serif and left', () => {
    expect(parseStreamOverlayOptions('?font=sans&align=center')).toMatchObject({
      font: 'sans',
      align: 'center',
    })
    expect(parseStreamOverlayOptions('?font=comic&align=right')).toMatchObject({
      font: 'serif',
      align: 'left',
    })
  })

  it('builds a short link that reads back as the same options', () => {
    expect(streamOverlayQuery(DEFAULT_STREAM_OVERLAY_OPTIONS)).toBe('?mode=obs')
    const options = {
      heightPct: 40,
      position: 'top',
      style: 'none',
      font: 'sans',
      align: 'center',
    } as const
    const query = streamOverlayQuery(options)
    expect(query).toBe('?mode=obs&height=40&position=top&bg=none&font=sans&align=center')
    expect(isStreamOverlay(query)).toBe(true)
    expect(parseStreamOverlayOptions(query)).toEqual(options)
  })

  it('is the console preview only with preview=1', () => {
    expect(isStreamOverlayPreview('?mode=obs&preview=1')).toBe(true)
    expect(isStreamOverlayPreview('?mode=obs')).toBe(false)
  })

  it('never goes below a stream-readable size', () => {
    // About 28 px on a 1080p stream (the projector allows 16 px).
    expect(streamFontBounds(1080)).toEqual({ min: 28, max: 65 })
  })
})

describe('fitting verses to the band', () => {
  it('shows every verse when they fit', () => {
    expect(fitVersesToBand(3, () => ({ fontSize: 40, fits: true }))).toEqual({
      count: 3,
      fontSize: 40,
    })
  })

  it('drops verses from the end when even the smallest size overflows', () => {
    // Only two of five verses fit at the minimum size.
    const fit = fitVersesToBand(5, count => ({ fontSize: 28, fits: count <= 2 }))
    expect(fit).toEqual({ count: 2, fontSize: 28 })
  })

  it('always shows at least the first verse', () => {
    const fit = fitVersesToBand(4, () => ({ fontSize: 28, fits: false }))
    expect(fit.count).toBe(1)
  })
})
