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
import { formatReference } from '../../shared/reference'
import type { AutoMode, BibleReference, OperatorAction, OperatorMessage } from '../../shared/types'
import { autoModeOption, coerceAutoMode } from '../autoMode'
import type { ConnectionState } from '../../components'

/** How long the button "pressed" feedback lasts after a confirmation. */
const FLASH_MS = 150

/** How long a notice stays up before it removes itself. */
const NOTICE_MS: Record<NoticeTone, number> = { success: 3000, info: 4000, error: 6000 }

/** Local storage key for operator token */
const TOKEN_STORAGE_KEY = 'biblelive_operator_token'

/** Which action button briefly pulses once the server confirms it. */
export type FlashTarget = 'approve' | 'clear' | 'clearPending'

/** How a notice reads: a confirmation, a neutral fact, or a failure. */
export type NoticeTone = 'success' | 'info' | 'error'

/** A single server-confirmed message for the operator. */
export interface OperatorNotice {
  /** Changes with every notice so repeating a message announces it again. */
  id: number
  tone: NoticeTone
  text: string
}

/**
 * Operator-facing wording for a failed action. The server's own message is kept
 * because it is already written for people ("Reference not found or already
 * processed"); the prefix names which control to try again.
 */
const ERROR_PREFIXES: Record<string, string> = {
  approve: 'Could not show that reference',
  clear: 'Could not clear the display',
  clear_pending: 'Could not clear the queue',
  set_verse_limit: 'Could not change the verses shown',
  set_display_theme: 'Could not change the background',
  set_auto_mode: 'Could not change auto-approve',
}

/**
 * Actions the console applies optimistically. When the server rejects one, the
 * control has to go back to the last confirmed value or it would keep claiming
 * a setting the display is not using.
 */
const OPTIMISTIC_ACTIONS = new Set(['set_verse_limit', 'set_display_theme', 'set_auto_mode'])

export interface OperatorState {
  /** Raw WebSocket status, for callers that need the exact socket state. */
  status: WsStatus
  /** WebSocket status mapped to the shared ConnectionIndicator vocabulary. */
  connectionState: ConnectionState
  /** True while the socket is open and actions can be sent. */
  isConnected: boolean
  /** Replace the socket now, for recovering without a page reload. */
  reconnect: () => void
  /** The server's answer to the operator's last action, or null when quiet. */
  notice: OperatorNotice | null
  dismissNotice: () => void
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
 *
 * Feedback follows the same rule: nothing is announced as done until the server
 * says so. Confirmations and errors land in `notice`; errors that follow an
 * optimistic settings change also put the confirmed value back.
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
  // Whether this session ever reached the server. A socket that opened and then
  // closed is a dropped connection, which is a different situation from a server
  // refusing us for want of a token.
  const [hasConnected, setHasConnected] = useState(false)
  const [notice, setNotice] = useState<OperatorNotice | null>(null)
  const flashTimerRef = useRef<number | undefined>(undefined)
  const noticeIdRef = useRef(0)
  const noticeTimerRef = useRef<number | undefined>(undefined)
  // The last values the server confirmed. Optimistic control changes fall back
  // to these when the server rejects them.
  const confirmedRef = useRef({ verseLimit, theme: displayTheme, autoMode })

  // Brief "pressed" pulse on a button once the server confirms its action.
  const pulse = useCallback((target: FlashTarget) => {
    setFlash(target)
    window.clearTimeout(flashTimerRef.current)
    flashTimerRef.current = window.setTimeout(() => setFlash(null), FLASH_MS)
  }, [])

  // One notice slot: a new message replaces the previous one instead of stacking,
  // so a busy queue cannot bury the working area in toasts.
  const showNotice = useCallback((tone: NoticeTone, text: string) => {
    noticeIdRef.current += 1
    setNotice({ id: noticeIdRef.current, tone, text })
    window.clearTimeout(noticeTimerRef.current)
    noticeTimerRef.current = window.setTimeout(() => setNotice(null), NOTICE_MS[tone])
  }, [])

  const dismissNotice = useCallback(() => {
    window.clearTimeout(noticeTimerRef.current)
    setNotice(null)
  }, [])

  useEffect(
    () => () => {
      window.clearTimeout(flashTimerRef.current)
      window.clearTimeout(noticeTimerRef.current)
    },
    []
  )


  /**
   * Keep a staged reference across a queue update when it is still queued.
   *
   * Detections arrive while an operator is deciding, and a new arrival used to
   * wipe the staged selection mid-approval. The reference is only ever approved
   * by ID, so holding on to it cannot show the wrong passage — and letting go
   * when its ID leaves the queue keeps the button in step with the list.
   */
  const keepStagedSelection = useCallback((next: BibleReference[]) => {
    setSelectedId(prevId =>
      prevId !== null && next.some(reference => reference.id === prevId) ? prevId : null
    )
  }, [])

  const handleMessage = (message: OperatorMessage) => {
    switch (message.type) {
      case 'state_snapshot': {
        setPending(message.data.pending)
        setHistory(message.data.history)
        setPendingCount(message.data.pending_count)
        setHistoryCount(message.data.history_count)
        setShownCount(message.data.history_count)
        setCurrentDisplay(message.data.current_display)
        setDisplayConnected(message.data.display_connections > 0)
        if (message.data.display_settings) {
          const verseLimit = coerceVerseLimit(message.data.display_settings.verse_limit)
          const theme = coerceDisplayTheme(message.data.display_settings.theme)
          setVerseLimit(verseLimit)
          setDisplayTheme(theme)
          confirmedRef.current.verseLimit = verseLimit
          confirmedRef.current.theme = theme
        }
        // Absent when talking to an older server, which leaves the mode off.
        if (message.data.auto_mode) {
          const autoMode = coerceAutoMode(message.data.auto_mode)
          setAutoMode(autoMode)
          confirmedRef.current.autoMode = autoMode
        }
        // A reconnect can land while something is staged; keep it if the queue
        // still holds it so a dropped socket does not undo the operator's work.
        keepStagedSelection(message.data.pending)
        break
      }
      case 'pending_update':
        setPending(message.data.pending)
        setPendingCount(message.data.count)
        keepStagedSelection(message.data.pending)
        break
      case 'approval_confirmed': {
        const shown = message.data.reference
        // The server pushes the approved reference; fold it into history and the
        // counters so the sidebar is not stale until the next snapshot.
        setHistory(prev => [shown, ...prev])
        setHistoryCount(count => count + 1)
        setShownCount(count => count + 1)
        setCurrentDisplay(shown)
        pulse('approve')
        showNotice('success', `Shown: ${formatReference(shown)}`)
        break
      }
      case 'clear_confirmed':
        setCurrentDisplay(null)
        pulse('clear')
        showNotice('info', 'Display cleared')
        break
      case 'pending_cleared':
        pulse('clearPending')
        showNotice('info', 'Pending queue cleared')
        break
      case 'connection_status':
        setDisplayConnected(message.data.display > 0)
        break
      case 'display_settings_confirmed': {
        const verseLimit = coerceVerseLimit(message.data.verse_limit)
        const theme = coerceDisplayTheme(message.data.theme)
        // The server echoes the whole settings block, so announce only what
        // actually moved; an unrelated echo stays quiet.
        const changes: string[] = []
        if (verseLimit !== confirmedRef.current.verseLimit) {
          changes.push(`verses shown ${verseLimit === 'all' ? 'All' : verseLimit}`)
        }
        if (theme !== confirmedRef.current.theme) {
          changes.push(`background ${theme === 'dark' ? 'Dark' : 'Light'}`)
        }
        setVerseLimit(verseLimit)
        setDisplayTheme(theme)
        confirmedRef.current.verseLimit = verseLimit
        confirmedRef.current.theme = theme
        if (changes.length > 0) showNotice('info', `Saved — ${changes.join(', ')}`)
        break
      }
      case 'auto_mode_confirmed': {
        const autoMode = coerceAutoMode(message.data.auto_mode)
        const changed = autoMode !== confirmedRef.current.autoMode
        setAutoMode(autoMode)
        confirmedRef.current.autoMode = autoMode
        if (changed) showNotice('info', `Auto-approve: ${autoModeOption(autoMode).label}`)
        break
      }
      case 'error': {
        const { action, message: reason } = message.data
        console.error('Server error:', reason)
        const prefix = ERROR_PREFIXES[action] ?? 'Action failed'
        showNotice(
          'error',
          OPTIMISTIC_ACTIONS.has(action)
            ? `${prefix} — reverted. ${reason}`
            : `${prefix}: ${reason}`
        )
        // An optimistic change is only provisional. Put the last confirmed value
        // back so the console matches what the display is really using.
        if (action === 'set_verse_limit') {
          setVerseLimit(confirmedRef.current.verseLimit)
          writeStoredVerseLimit(confirmedRef.current.verseLimit, window.localStorage)
        } else if (action === 'set_display_theme') {
          setDisplayTheme(confirmedRef.current.theme)
          writeStoredDisplayTheme(confirmedRef.current.theme, window.localStorage)
        } else if (action === 'set_auto_mode') {
          setAutoMode(confirmedRef.current.autoMode)
        }
        break
      }
    }
  }

  const { status, send, reconnect } = useWebSocket<OperatorMessage>(
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

  // Remember a successful connection: it is what separates a dropped socket (the
  // console stays, with a reconnect banner) from a server that wants a token.
  useEffect(() => {
    if (status === 'open') setHasConnected(true)
  }, [status])

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
    reconnect,
    notice,
    dismissNotice,
    // A rejected token (close 1008) means the operator must supply one. A socket
    // that never opened and has no token is treated the same way, so the console
    // does not flash before the server's refusal arrives — but once the session
    // has connected, a close is a dropped connection, not an auth failure, and
    // the console stays up with the reconnect banner instead of a dead-end
    // token prompt.
    needsToken: authFailed || (status === 'closed' && !token && !hasConnected),
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
