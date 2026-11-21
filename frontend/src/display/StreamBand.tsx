import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { formatReference } from '../shared/reference'
import { useI18n } from '../shared/i18nContext'
import { hiddenVerseCount, selectVerses } from '../shared/verseText'
import type { VerseLimit } from '../shared/verseText'
import type { BibleReference } from '../shared/types'
import { computeFitFontSize, fitFontSizeByMeasurement } from './fitText'
import { fitVersesToBand, streamFontBounds } from './streamOverlayOptions'
import type { StreamOverlayOptions } from './streamOverlayOptions'
import { SCREEN_SWAP, swapStyle } from './fadeTimings'
import type { SwapTiming } from './fadeTimings'
import '../styles/tokens.css'
import '../styles/global.css'
import './streamOverlay.css'

/** Line-height multiplier of the verse text. */
const VERSE_LINE_HEIGHT = 1.3

export interface StreamBandProps {
  reference: BibleReference | null
  visible: boolean
  /** The current swap's exit and entrance durations. */
  timing?: SwapTiming
  verseLimit: VerseLimit
  options: StreamOverlayOptions
}

/**
 * The lower third itself, on a transparent page: the stream overlay draws it from
 * the server's messages, the console's preview from sample passages.
 */
export function StreamBand({
  reference,
  visible,
  timing = SCREEN_SWAP,
  verseLimit,
  options,
}: StreamBandProps) {
  const { t } = useI18n()
  const [shownCount, setShownCount] = useState(0)
  const versesRef = useRef<HTMLDivElement>(null)

  // OBS composites the page over the camera: nothing may paint the background.
  // display.html sets the projector's dark color-scheme before first paint; an iframe
  // whose color-scheme differs from its embedder's gets an opaque backdrop, which
  // would hide the camera behind the console's preview, so the overlay drops it.
  useEffect(() => {
    const root = document.documentElement
    const scheme = root.style.colorScheme
    root.classList.add('stream-overlay-page')
    root.style.colorScheme = 'normal'
    return () => {
      root.classList.remove('stream-overlay-page')
      root.style.colorScheme = scheme
    }
  }, [])

  const verses = selectVerses(reference, verseLimit)
  const limitHidden = hiddenVerseCount(reference, verseLimit)

  // Fit the verses into the band: shrink the text down to a size a stream viewer
  // can still read, and past that drop verses from the end rather than overflow.
  useLayoutEffect(() => {
    const block = versesRef.current
    if (!block || verses.length === 0) return
    const inner = block.querySelector('.stream-verses-inner') as HTMLElement | null
    const rows = Array.from(block.querySelectorAll<HTMLElement>('.stream-verse'))

    const compute = () => {
      const bounds = streamFontBounds(window.innerHeight)
      const apply = (size: number) => {
        block.style.fontSize = `${size}px`
      }
      const fitAt = (count: number) => {
        rows.forEach((row, i) => {
          row.style.display = i < count ? '' : 'none'
        })
        const shown = verses.slice(0, count)
        const estimate = computeFitFontSize({
          charCount: shown.reduce((sum, v) => sum + v.text.length, 0),
          lineCount: shown.length,
          widthPx: block.clientWidth,
          heightPx: block.clientHeight,
          lineHeight: VERSE_LINE_HEIGHT,
          chromePx: 0,
          minFontSize: bounds.min,
          maxFontSize: bounds.max,
        })
        const measure = () => inner?.offsetHeight ?? 0
        const fontSize = fitFontSizeByMeasurement(
          estimate,
          apply,
          measure,
          block.clientHeight,
          bounds
        )
        apply(fontSize)
        // Not laid out (jsdom, hidden source): everything counts as fitting.
        return { fontSize, fits: block.clientHeight <= 0 || measure() <= block.clientHeight }
      }
      const fit = fitVersesToBand(rows.length, fitAt)
      fitAt(fit.count)
      setShownCount(fit.count)
    }

    compute()
    const observer = new ResizeObserver(compute)
    observer.observe(block)
    return () => observer.disconnect()
    // The font and alignment change how much text fits, so they refit too.
  }, [verses, options.font, options.align])

  const droppedCount = limitHidden + Math.max(0, verses.length - (shownCount || verses.length))
  const bandClass = [
    'stream-band',
    `stream-band--${options.position}`,
    `stream-band--${options.style}`,
    `stream-band--${options.font}`,
    `stream-band--${options.align}`,
    visible ? 'stream-band--visible' : 'stream-band--hidden',
  ].join(' ')

  return (
    <div className="stream-overlay" data-testid="stream-overlay">
      {reference && (
        <div
          className={bandClass}
          style={{ height: `${options.heightPct}vh`, ...swapStyle(timing) }}
          data-testid="stream-band"
        >
          <div className="stream-reference">
            {formatReference(reference)}
            {droppedCount > 0 && (
              <span className="stream-more"> {t('stage.moreVerses', { count: droppedCount })}</span>
            )}
          </div>
          <div className="stream-verses" ref={versesRef} data-testid="stream-verses">
            <div className="stream-verses-inner">
              {verses.map(verse => (
                <p className="stream-verse" key={verse.verse}>
                  <span className="stream-verse-number" aria-hidden="true">
                    {verse.verse}
                  </span>
                  <span className="stream-verse-text">{verse.text}</span>
                </p>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
