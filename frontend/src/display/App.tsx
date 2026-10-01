import { useEffect, useRef, useState } from 'react'
import { useWebSocket } from '../shared/useWebSocket'
import { formatReference } from '../shared/reference'
import type { BibleReference, DisplayMessage } from '../shared/types'
import { useTheme } from '../hooks'
import '../styles/tokens.css'
import '../styles/global.css'
import './display.css'

/** How long the fade-out animation runs before content is swapped. */
const FADE_MS = 400

export default function App() {
  const [reference, setReference] = useState<BibleReference | null>(null)
  const [visible, setVisible] = useState(false)
  const [status, setStatus] = useState('Connecting to BibleLive server...')
  const swapTimerRef = useRef<number | undefined>(undefined)
  
  // Independent theme for display (can differ from operator console)
  const { resolvedTheme } = useTheme()

  const handleMessage = (message: DisplayMessage) => {
    // A newer message always wins; cancel any in-flight content swap.
    window.clearTimeout(swapTimerRef.current)

    if (message.type === 'reference') {
      const next = message.data
      // Fade out whatever is showing, then swap in the new reference.
      setVisible(false)
      swapTimerRef.current = window.setTimeout(() => {
        setReference(next)
        setVisible(true)
        setStatus('')
      }, FADE_MS)
    } else {
      // 'clear': fade out, then return to the waiting state.
      setVisible(false)
      swapTimerRef.current = window.setTimeout(() => {
        setReference(null)
        setStatus('Waiting for Bible reference...')
      }, FADE_MS)
    }
  }

  const { status: wsStatus } = useWebSocket<DisplayMessage>(
    '/ws/display',
    handleMessage,
  )

  // Reflect connection changes in the status line. A reference that is
  // already on screen keeps the status line empty across reconnects.
  useEffect(() => {
    if (wsStatus === 'open') {
      setStatus(reference ? '' : 'Connected to BibleLive server')
    } else if (wsStatus === 'closed') {
      setStatus('Disconnected from server...')
    } else {
      setStatus('Connecting to BibleLive server...')
    }
  }, [wsStatus, reference])

  // Cancel a pending swap when the page goes away.
  useEffect(
    () => () => window.clearTimeout(swapTimerRef.current),
    [],
  )

  const fadeClass = visible ? 'display-fade-in' : 'display-fade-out'

  return (
    <div className="display-app">
      {reference ? (
        <div className={`display-content ${fadeClass}`}>
          {/* Verse text - primary focus */}
          <div className="display-verse">
            {reference.text}
          </div>
          
          {/* Reference - secondary, elegant label below */}
          <div className="display-reference">
            {formatReference(reference)}
          </div>
          
          {/* Optional translation indicator */}
          {/* <div className="display-translation">KJV</div> */}
        </div>
      ) : (
        <div className="display-waiting">
          <div className="display-waiting-icon" aria-hidden="true">
            <svg width="64" height="64" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
              <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" />
              <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" />
            </svg>
          </div>
          <p className="display-waiting-text">{status}</p>
        </div>
      )}
      
      {/* Theme indicator (subtle, for setup purposes) */}
      <div className="display-theme-badge" aria-label={`Theme: ${resolvedTheme}`}>
        {resolvedTheme === 'dark' ? (
          <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
            <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
          </svg>
        ) : (
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <circle cx="12" cy="12" r="5" />
            <line x1="12" y1="1" x2="12" y2="3" />
            <line x1="12" y1="21" x2="12" y2="23" />
            <line x1="4.22" y1="4.22" x2="5.64" y2="5.64" />
            <line x1="18.36" y1="18.36" x2="19.78" y2="19.78" />
            <line x1="1" y1="12" x2="3" y2="12" />
            <line x1="21" y1="12" x2="23" y2="12" />
            <line x1="4.22" y1="19.78" x2="5.64" y2="18.36" />
            <line x1="18.36" y1="5.64" x2="19.78" y2="4.22" />
          </svg>
        )}
      </div>
    </div>
  )
}
