import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { useWebSocket } from '../shared/useWebSocket'
import { formatReference } from '../shared/reference'
import {
  coerceVerseLimit,
  hiddenVerseCount,
  readStoredVerseLimit,
  selectVerses,
} from '../shared/verseText'
import type { VerseLimit } from '../shared/verseText'
import type { BibleReference, DisplayMessage } from '../shared/types'
import { computeFitFontSize, fitFontSizeByMeasurement } from './fitText'
import {
  fitVersesToBand,
  parseStreamOverlayOptions,
  streamFontBounds,
} from './streamOverlayOptions'
import '../styles/tokens.css'
import '../styles/global.css'
import './streamOverlay.css'

/** How long the fade-out runs before content is swapped (as on the projector). */
const FADE_MS = 400

/** Line-height multiplier of the verse text. */
const VERSE_LINE_HEIGHT = 1.3

/**
 * The passage on screen as a lower third for a stream: `/display?mode=obs`,
 * added in OBS as a Browser source over the camera. It shows what the projector
 * shows (same messages, same verse limit) on a transparent page.
 */
export default function StreamOverlay() {
  const [options] = useState(() => parseStreamOverlayOptions(window.location.search))
  const [reference, setReference] = useState<BibleReference | null>(null)
  const [visible, setVisible] = useState(false)
  const [verseLimit, setVerseLimit] = useState<VerseLimit>(() =>
    coerceVerseLimit(readStoredVerseLimit(window.localStorage))
  )
  const [shownCount, setShownCount] = useState(0)
  const swapTimerRef = useRef<number | undefined>(undefined)
  const versesRef = useRef<HTMLDivElement>(null)

  const handleMessage = (message: DisplayMessage) => {
    if (message.type === 'display_settings') {
      // The projector's background theme does not apply: the band has its own look.
      setVerseLimit(coerceVerseLimit(message.data.verse_limit))
      return
    }
    window.clearTimeout(swapTimerRef.current)
    setVisible(false)
    const next = message.type === 'reference' ? message.data : null
    swapTimerRef.current = window.setTimeout(() => {
      setReference(next)
      setVisible(next !== null)
    }, FADE_MS)
  }

  // Like the projector, the overlay must survive server restarts unattended.
  useWebSocket<DisplayMessage>('/ws/display', handleMessage, {
    maxReconnectAttempts: Infinity,
  })

  useEffect(() => () => window.clearTimeout(swapTimerRef.current), [])

  // OBS composites the page over the camera: nothing may paint the background.
  useEffect(() => {
    document.documentElement.classList.add('stream-overlay-page')
    return () => document.documentElement.classList.remove('stream-overlay-page')
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
  }, [verses])

  const droppedCount = limitHidden + Math.max(0, verses.length - (shownCount || verses.length))
  const bandClass = [
    'stream-band',
    `stream-band--${options.position}`,
    `stream-band--${options.style}`,
    visible ? 'stream-band--visible' : '',
  ]
    .filter(Boolean)
    .join(' ')

  return (
    <div className="stream-overlay" data-testid="stream-overlay">
      {reference && (
        <div
          className={bandClass}
          style={{ height: `${options.heightPct}vh` }}
          data-testid="stream-band"
        >
          <div className="stream-reference">
            {formatReference(reference)}
            {droppedCount > 0 && (
              <span className="stream-more">
                {' '}
                +{droppedCount} more verse{droppedCount === 1 ? '' : 's'}
              </span>
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
