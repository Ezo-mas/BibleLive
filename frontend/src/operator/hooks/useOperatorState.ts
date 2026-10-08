import { useCallback, useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { useWebSocket } from '../../shared/useWebSocket'
import type { WsStatus } from '../../shared/useWebSocket'
import {
  coerceDisplayTheme,
  coerceVerseLimit,
  readStoredDisplayTheme,
  readStoredVerseLimit,
  writeStoredDisplayTheme,
  writeStoredVerseLimit,
} from '../../shared/verseText'
import type { DisplayTheme, VerseLimit } from '../../shared/verseText'
import type { AutoMode, BibleReference, OperatorAction, OperatorMessage } from '../../shared/types'
import { coerceAutoMode } from '../autoMode'
import type { ConnectionState } from '../../components'

/** How long the button "pressed" feedback lasts after a confirmation. */
const FLASH_MS = 150

/** Local storage key for operator token */
const TOKEN_STORAGE_KEY = 'biblelive_operator_token'

/** Which action button briefly pulses once the server confirms it. */
export type FlashTarget = 'approve' | 'clear' | 'clearPending'

export interface OperatorState {
  /** Raw WebSocket status, for callers that need the exact socket state. */
  status: WsStatus
  /** WebSocket status mapped to the shared ConnectionIndicator vocabulary. */
  connectionState: ConnectionState
  /** True while the socket is open and actions can be sent. */
  isConnected: boolean
  /** True when the operator must (re)enter a token before anything else. */
  needsToken: boolean
  authFailed: boolean
  tokenInput: string
  setTokenInput: (value: string) => void
  submitToken: (event: FormEvent) => void
  logout: () => void
  isAuthenticated: boolean

  pending: BibleReference[]
  history: BibleReference[]
  pendingCount: number
  historyCount: number
  shownCount: number
  /** Scripture currently on the display, as reported by the server. */
  currentDisplay: BibleReference | null
  hasDisplayContent: boolean
  displayConnected: boolean
  /**
   * NEXT: the reference staged for the projector. Client-side only — staging
   * previews a reference and never touches the display.
   */
  nextReference: BibleReference | null
  showNext: () => void
  clearNext: () => void
  canShowNext: boolean

  /** Reference ID of the selected pending item, or null when nothing is selected. */
  selectedId: string | null
  selectPending: (id: string) => void
  selectNext: () => void
  selectPrevious: () => void
  deselect: () => void

  approveSelected: () => void
  clearDisplay: () => void
  clearPendingQueue: () => void
  canApprove: boolean
  canClearDisplay: boolean
  canClearPending: boolean
  flash: FlashTarget | null

  verseLimit: VerseLimit
  displayTheme: DisplayTheme
  setDisplayVerseLimit: (next: VerseLimit) => void
  setDisplayBackground: (next: DisplayTheme) => void

  /** Which new detections the server displays without waiting for approval. */
  autoMode: AutoMode
  setAutoApproveMode: (next: AutoMode) => void
}

/**
 * Own the operator console's server state and the actions that mutate it.
 *
 * Approval is always sent by reference ID, never by queue position, so a queue
 * update between selecting and approving cannot display the wrong passage. The
 * server stays authoritative: every action is a request whose result arrives
 * back over the socket, and a reconnect re-reads the state snapshot.
 */
export function useOperatorState(): OperatorState {
  const [token, setToken] = useState<string | null>(() => localStorage.getItem(TOKEN_STORAGE_KEY))
  const [tokenInput, setTokenInput] = useState('')
  const [authFailed, setAuthFailed] = useState(false)

  const [pending, setPending] = useState<BibleReference[]>([])
  const [history, setHistory] = useState<BibleReference[]>([])
  const [pendingCount, setPendingCount] = useState(0)
  const [historyCount, setHistoryCount] = useState(0)
  const [shownCount, setShownCount] = useState(0)
  const [currentDisplay, setCurrentDisplay] = useState<BibleReference | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [flash, setFlash] = useState<FlashTarget | null>(null)
  const [displayConnected, setDisplayConnected] = useState(false)
  // The display is presentation-only, so this control lives here and the server
  // pushes the value to every projector. localStorage keeps the last choice
  // while the socket reconnects.
  const [verseLimit, setVerseLimit] = useState<VerseLimit>(() =>
    coerceVerseLimit(readStoredVerseLimit(window.localStorage))
  )
  // Display background. Projectors run dark, so that is the default here too.
  const [displayTheme, setDisplayTheme] = useState<DisplayTheme>(() =>
    coerceDisplayTheme(readStoredDisplayTheme(window.localStorage))
  )
  // Auto-approve mode. Server-side state (persisted there too), so the console
  // only mirrors it: the snapshot on connect is the source of truth.
  const [autoMode, setAutoMode] = useState<AutoMode>('off')
  const flashTimerRef = useRef<number | undefined>(undefined)

  // Brief "pressed" pulse on a button once the server confirms its action.
  const pulse = useCallback((target: FlashTarget) => {
    setFlash(target)
    window.clearTimeout(flashTimerRef.current)
    flashTimerRef.current = window.setTimeout(() => setFlash(null), FLASH_MS)
  }, [])

  useEffect(() => () => window.clearTimeout(flashTimerRef.current), [])

  const handleMessage = (message: OperatorMessage) => {
    switch (message.type) {
      case 'state_snapshot':
        setPending(message.data.pending)
        setHistory(message.data.history)
        setPendingCount(message.data.pending_count)
        setHistoryCount(message.data.history_count)
        setShownCount(message.data.history_count)
        setCurrentDisplay(message.data.current_display)
        setDisplayConnected(message.data.display_connections > 0)
        if (message.data.display_settings) {
          setVerseLimit(coerceVerseLimit(message.data.display_settings.verse_limit))
          setDisplayTheme(coerceDisplayTheme(message.data.display_settings.theme))
        }
        // Absent when talking to an older server, which leaves the mode off.
        if (message.data.auto_mode) {
          setAutoMode(coerceAutoMode(message.data.auto_mode))
        }
        setSelectedId(null)
        break
      case 'pending_update':
        setPending(message.data.pending)
        setPendingCount(message.data.count)
        setSelectedId(null)
        break
      case 'approval_confirmed':
        // The server pushes the approved reference; fold it into history and the
        // counters so the sidebar is not stale until the next snapshot.
        setHistory(prev => [message.data.reference, ...prev])
        setHistoryCount(count => count + 1)
        setShownCount(count => count + 1)
        setCurrentDisplay(message.data.reference)
        pulse('approve')
        break
      case 'clear_confirmed':
        setCurrentDisplay(null)
        pulse('clear')
        break
      case 'pending_cleared':
        pulse('clearPending')
        break
      case 'connection_status':
        setDisplayConnected(message.data.display > 0)
        break
      case 'display_settings_confirmed':
        setVerseLimit(coerceVerseLimit(message.data.verse_limit))
        setDisplayTheme(coerceDisplayTheme(message.data.theme))
        break
      case 'auto_mode_confirmed':
        setAutoMode(coerceAutoMode(message.data.auto_mode))
        break
      case 'error':
        console.error('Server error:', message.data.message)
        break
    }
  }

  const { status, send } = useWebSocket<OperatorMessage>(
    token ? `/ws/operator?token=${encodeURIComponent(token)}` : '/ws/operator',
    handleMessage,
    {
      onClose: event => {
        // WebSocket close code 1008 = policy violation (auth failure)
        if (event.code === 1008) {
          setAuthFailed(true)
          // Clear invalid token
          localStorage.removeItem(TOKEN_STORAGE_KEY)
          setToken(null)
        }
      },
    }
  )

  const handleTokenSubmit = (e: FormEvent) => {
    e.preventDefault()
    const trimmed = tokenInput.trim()
    if (trimmed) {
      localStorage.setItem(TOKEN_STORAGE_KEY, trimmed)
      setToken(trimmed)
      setTokenInput('')
      setAuthFailed(false)
    }
  }

  const logout = () => {
    localStorage.removeItem(TOKEN_STORAGE_KEY)
    setToken(null)
    setAuthFailed(false)
  }

  const sendAction = useCallback(
    (action: OperatorAction) => {
      send(action)
    },
    [send]
  )

  const approveSelected = useCallback(() => {
    if (selectedId !== null) {
      sendAction({ action: 'approve', reference_id: selectedId })
    }
  }, [selectedId, sendAction])

  const clearDisplay = useCallback(() => {
    sendAction({ action: 'clear' })
  }, [sendAction])

  const clearPendingQueue = useCallback(() => {
    if (window.confirm('Clear all pending references?')) {
      sendAction({ action: 'clear_pending' })
    }
  }, [sendAction])

  // Applied optimistically so the control feels immediate; the server echoes
  // the accepted value back in display_settings_confirmed.
  const setDisplayVerseLimit = useCallback(
    (next: VerseLimit) => {
      setVerseLimit(next)
      writeStoredVerseLimit(next, window.localStorage)
      sendAction({ action: 'set_verse_limit', verse_limit: next })
    },
    [sendAction]
  )

  const setDisplayBackground = useCallback(
    (next: DisplayTheme) => {
      setDisplayTheme(next)
      writeStoredDisplayTheme(next, window.localStorage)
      sendAction({ action: 'set_display_theme', theme: next })
    },
    [sendAction]
  )

  // Applied optimistically so the switch feels immediate; the server echoes the
  // accepted mode back in auto_mode_confirmed.
  const setAutoApproveMode = useCallback(
    (next: AutoMode) => {
      setAutoMode(next)
      sendAction({ action: 'set_auto_mode', auto_mode: next })
    },
    [sendAction]
  )

  const selectNext = useCallback(() => {
    setSelectedId(prevId => {
      if (pending.length === 0) return null
      if (prevId === null) return pending[0].id
      const index = pending.findIndex(ref => ref.id === prevId)
      if (index === -1) return pending[0].id
      return pending[Math.min(index + 1, pending.length - 1)].id
    })
  }, [pending])

  const selectPrevious = useCallback(() => {
    setSelectedId(prevId => {
      if (pending.length === 0) return null
      if (prevId === null) return pending[0].id
      const index = pending.findIndex(ref => ref.id === prevId)
      if (index === -1) return pending[0].id
      return pending[Math.max(index - 1, 0)].id
    })
  }, [pending])

  const deselect = useCallback(() => setSelectedId(null), [])

  // NEXT is the staged scripture. It is derived from the queue rather than kept
  // as its own state so it cannot drift from the list the operator is looking
  // at: a queue update, an approval, or Esc drops it. Show is the ordinary
  // approval of a queued reference, so the display still only moves through the
  // server's approval path.
  const nextReference = pending.find(reference => reference.id === selectedId) ?? null

  const showNext = useCallback(() => {
    if (nextReference) {
      sendAction({ action: 'approve', reference_id: nextReference.id })
    }
  }, [nextReference, sendAction])

  const isConnected = status === 'open'
  const connectionState: ConnectionState = isConnected
    ? 'connected'
    : status === 'connecting'
      ? 'connecting'
      : 'disconnected'

  return {
    status,
    connectionState,
    isConnected,
    // Without a token the socket stays closed; with a rejected one the server
    // closes with 1008. Either way the operator must supply a token first.
    needsToken: authFailed || (status === 'closed' && !token),
    authFailed,
    tokenInput,
    setTokenInput,
    submitToken: handleTokenSubmit,
    logout,
    isAuthenticated: token !== null,

    pending,
    history,
    pendingCount,
    historyCount,
    shownCount,
    currentDisplay,
    hasDisplayContent: currentDisplay !== null,
    displayConnected,
    nextReference,
    showNext,
    clearNext: deselect,
    canShowNext: nextReference !== null && isConnected,

    selectedId,
    selectPending: setSelectedId,
    selectNext,
    selectPrevious,
    deselect,

    approveSelected,
    clearDisplay,
    clearPendingQueue,
    canApprove: selectedId !== null && isConnected,
    canClearDisplay: currentDisplay !== null && isConnected,
    canClearPending: pending.length > 0 && isConnected,
    flash,

    verseLimit,
    displayTheme,
    setDisplayVerseLimit,
    setDisplayBackground,

    autoMode,
    setAutoApproveMode,
  }
}
