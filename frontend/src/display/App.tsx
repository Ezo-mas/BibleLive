import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { useWebSocket } from '../shared/useWebSocket'
import { formatReference } from '../shared/reference'
import {
  coerceDisplayTheme,
  coerceVerseLimit,
  hiddenVerseCount,
  readStoredVerseLimit,
  selectVerses,
  writeStoredDisplayTheme,
} from '../shared/verseText'
import type { DisplayTheme, VerseLimit } from '../shared/verseText'
import type { BibleReference, DisplayMessage } from '../shared/types'
import { computeFitFontSize, fitFontSizeByMeasurement, MAX_FONT_SIZE } from './fitText'
import '../styles/tokens.css'
import '../styles/global.css'
import './display.css'

/** How long the fade-out animation runs before content is swapped. */
const FADE_MS = 400

/** Line-height multiplier applied to the fitted verse font size. */
const VERSE_LINE_HEIGHT = 1.35

export default function App() {
  const [reference, setReference] = useState<BibleReference | null>(null)
  const [visible, setVisible] = useState(false)
  // Presentation-only: the operator console owns this value and the server
  // pushes it here. localStorage is only a pre-socket fallback for the first
  // render, so a reconnect does not flash a stale verse count.
  const [verseLimit, setVerseLimit] = useState<VerseLimit>(() =>
    coerceVerseLimit(readStoredVerseLimit(window.localStorage))
  )
  // Dark by default: a white projector washes out in a lit room. The operator
  // owns the choice and the server pushes it here like the verse limit.
  const [theme, setTheme] = useState<DisplayTheme>(() =>
    document.documentElement.getAttribute('data-theme') === 'light' ? 'light' : 'dark'
  )
  const [fontSize, setFontSize] = useState(MAX_FONT_SIZE)
  const swapTimerRef = useRef<number | undefined>(undefined)
  const stageRef = useRef<HTMLDivElement>(null)
  const versesRef = useRef<HTMLDivElement>(null)

  const handleMessage = (message: DisplayMessage) => {
    if (message.type === 'display_settings') {
      setVerseLimit(coerceVerseLimit(message.data.verse_limit))
      setTheme(coerceDisplayTheme(message.data.theme))
      return
    }

    // A newer message always wins; cancel any in-flight content swap.
    window.clearTimeout(swapTimerRef.current)

    if (message.type === 'reference') {
      const next = message.data
      // Fade out whatever is showing, then swap in the new reference.
      setVisible(false)
      swapTimerRef.current = window.setTimeout(() => {
        setReference(next)
        setVisible(true)
      }, FADE_MS)
    } else {
      // 'clear': fade out, then return to the waiting state.
      setVisible(false)
      swapTimerRef.current = window.setTimeout(() => {
        setReference(null)
      }, FADE_MS)
    }
  }

  // The display must outlive server restarts and network blips: a projector
  // that goes dark mid-service cannot be recovered without manual refresh, so
  // keep retrying indefinitely (the operator console keeps the finite budget
  // so auth failures can surface the token prompt).
  useWebSocket<DisplayMessage>('/ws/display', handleMessage, {
    maxReconnectAttempts: Infinity,
  })

  // Cancel a pending swap when the page goes away.
  useEffect(() => () => window.clearTimeout(swapTimerRef.current), [])

  // The theme lives on <html> so the design tokens resolve for the whole page.
  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
    document.documentElement.style.colorScheme = theme
    // Persisted so a reload paints the right background before the socket
    // delivers the server value.
    writeStoredDisplayTheme(theme, window.localStorage)
  }, [theme])

  // Keyboard shortcut for the operator standing at the projector: T toggles the
  // background. The page stays presentation-only; the server setting still wins
  // on the next display_settings broadcast.
  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 't' && event.key !== 'T') return
      if (event.metaKey || event.ctrlKey || event.altKey) return
      setTheme(current => (current === 'dark' ? 'light' : 'dark'))
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [])

  const visibleVerses = selectVerses(reference, verseLimit)
  const hiddenCount = hiddenVerseCount(reference, verseLimit)

  // Shrink the font until the visible verses fit the viewport, and re-fit on
  // resize. Projectors have no scrollbar, so overflow is never acceptable.
  useLayoutEffect(() => {
    const stage = stageRef.current
    const block = versesRef.current
    if (!stage || !block || visibleVerses.length === 0) return

    const compute = () => {
      const rect = stage.getBoundingClientRect()
      const estimate = computeFitFontSize({
        charCount: visibleVerses.reduce((sum, v) => sum + v.text.length, 0),
        lineCount: visibleVerses.length,
        widthPx: rect.width,
        heightPx: rect.height,
        lineHeight: VERSE_LINE_HEIGHT,
      })

      const inner = block.querySelector('.display-verses-inner') as HTMLElement | null
      // `block` is stretched to the leftover height, so the overflow signal is
      // the inner wrapper's natural height against the space `block` occupies.
      const apply = (size: number) => {
        block.style.fontSize = `${size}px`
      }
      const fitted = fitFontSizeByMeasurement(
        estimate,
        apply,
        () => inner?.offsetHeight ?? 0,
        block.clientHeight
      )
      apply(fitted)
      setFontSize(current => (current === fitted ? current : fitted))
    }

    compute()
    const observer = new ResizeObserver(compute)
    observer.observe(stage)
    return () => observer.disconnect()
  }, [visibleVerses, theme])

  const fadeClass = visible ? 'display-fade-in' : 'display-fade-out'

  return (
    <div className="display-app">
      {/* Measured stage: the verse text is sized to fit exactly this box. */}
      <div className="display-stage" ref={stageRef}>
        {reference ? (
          <div className={`display-content ${fadeClass}`}>
            {/* Verse text - primary focus, bounded to the chosen limit */}
            <div
              className="display-verses"
              ref={versesRef}
              style={{ fontSize: `${fontSize}px` }}
              data-testid="display-verses"
            >
              <div className="display-verses-inner">
                {visibleVerses.map(verse => (
                  <p className="display-verse" key={verse.verse}>
                    <span className="display-verse-number" aria-hidden="true">
                      {verse.verse}
                    </span>
                    <span className="display-verse-text">{verse.text}</span>
                  </p>
                ))}
              </div>
            </div>

            {/* Reference - secondary, elegant label below */}
            <div className="display-reference">{formatReference(reference)}</div>

            {/* Tell the audience when a range was only partly shown */}
            {hiddenCount > 0 && (
              <div className="display-range-note">
                +{hiddenCount} more verse{hiddenCount === 1 ? '' : 's'}
              </div>
            )}
          </div>
        ) : (
          <div className="display-waiting">
            <div className="display-waiting-icon" aria-hidden="true">
              <svg
                width="64"
                height="64"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.5"
              >
                <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" />
                <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" />
              </svg>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
