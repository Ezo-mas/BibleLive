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
import type {
  AuthenticateAction,
  AutoMode,
  BibleReference,
  OperatorAction,
  OperatorMessage,
  Passage,
  PipelineStatus,
} from '../../shared/types'
import { passageToReference } from '../passageApi'
import type { PassageQuery } from '../passageApi'
import { autoModeOption, coerceAutoMode } from '../autoMode'
import { useScriptureSearch } from './useScriptureSearch'
import { usePipelineStatus } from './usePipelineStatus'
import type { ScriptureSearchState } from './useScriptureSearch'
import { useSpokenLanguage } from './useSpokenLanguage'
import { useBibleVersion } from './useBibleVersion'
import type { BibleVersionState } from './useBibleVersion'
import type { SpokenLanguageState } from './useSpokenLanguage'
import type { ConnectionState } from '../../components'

/** How long the button "pressed" feedback lasts after a confirmation. */
const FLASH_MS = 150

/** How long a notice stays up before it removes itself. */
const NOTICE_MS: Record<NoticeTone, number> = { success: 3000, info: 4000, error: 6000 }

/** Local storage key for operator token */
const TOKEN_STORAGE_KEY = 'biblelive_operator_token'

/**
 * How many history entries a state snapshot carries (the server sends the last
 * 20, see `send_operator_state_snapshot` in `core/server/state.py`). The console
 * keeps the same window, so an entry folded in from a confirmation cannot grow
 * the list past what a reconnect would show. `history_count` still reports the
 * server's total.
 */
const HISTORY_WINDOW = 20

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
  display_reference: 'Could not show that passage',
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

/**
 * What Up Next is holding: a queued detection the operator selected, a passage
 * they picked by hand, or an entry from display history they want to show again.
 * The source is explicit rather than inferred from timing or which component last
 * rendered, because Show means two different things — approving a reference by
 * ID, or asking the server to display a passage it resolved from coordinates.
 *
 * A history entry is held as its own snapshot, taken when the operator clicked
 * it, rather than as an ID to look up again later. History changes underneath a
 * staged selection (entries age out of the server's window), and Show sends
 * coordinates, so holding the entry itself is what keeps the wrong passage off
 * the display regardless of how the list moves.
 */
export type StagedReference =
  | { kind: 'pending'; id: string }
  | { kind: 'manual'; passage: Passage }
  | { kind: 'history'; reference: BibleReference }

/** Where the staged scripture came from, for the panel's badge. */
export type NextSource = 'detected' | 'manual' | 'history' | null

/**
 * Whether a confirmation is about the passage currently staged.
 *
 * A manual passage and a history entry are both identified by their coordinates:
 * neither ever entered the queue and the server assigns the reference ID only
 * when it displays the passage, so coordinates are what the request and the
 * confirmation have in common. This is what stops a late confirmation for one
 * passage from clearing a different one the operator staged in the meantime.
 * A queued selection is matched by the queue update that follows an approval,
 * never by coordinates.
 */
/**
 * The verse a reference covers at its right end.
 *
 * A single-verse reference reaches the console in two shapes: a detection that
 * named one verse is stored as-is (``verse_end: null``), while the server states
 * the same passage as ``verse_start === verse_end`` when it resolves one. They
 * are the same passage, and only comparing them shape-blind would make a replay
 * of a detected verse look like someone else's confirmation.
 */
function referenceEnd(reference: {
  verse_start: number | null
  verse_end: number | null
}): number | null {
  return reference.verse_end ?? reference.verse_start
}

function isStagedPassage(staged: StagedReference, shown: BibleReference): boolean {
  if (staged.kind === 'pending') return false
  const coordinates = staged.kind === 'manual' ? staged.passage : staged.reference
  return (
    coordinates.book === shown.book &&
    coordinates.chapter === shown.chapter &&
    coordinates.verse_start === shown.verse_start &&
    referenceEnd(coordinates) === referenceEnd(shown)
  )
}

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
  /** Scripture currently on the display, as reported by the server. */
  currentDisplay: BibleReference | null
  hasDisplayContent: boolean
  displayConnected: boolean
  /** Audio pipeline health; null while disconnected or before the first answer. */
  pipeline: PipelineStatus | null
  /**
   * NEXT: the scripture staged for the projector, whether it came from the
   * pending queue or from a manual selection. Client-side only — staging
   * previews a passage and never touches the display.
   */
  nextReference: BibleReference | null
  nextSource: NextSource
  showNext: () => void
  clearNext: () => void
  canShowNext: boolean
  /**
   * Send a resolved manual passage straight to the display without staging it.
   * The same `display_reference` request `showNext` sends for a staged passage —
   * coordinates only, for the server to resolve — used by Shift+Enter in Find
   * Scripture. Nothing is staged and Up Next is untouched.
   */
  showManualDirect: (request: PassageQuery) => void

  /** Manual passage search: the book list and the lookup the form calls. */
  search: ScriptureSearchState

  /**
   * The audio pipeline's spoken language, read and switched through the server.
   * Not part of the WebSocket protocol: it is a plain HTTP request to the
   * server, which forwards it to the pipeline.
   */
  spokenLanguage: SpokenLanguageState

  /**
   * The Bible translation verse text comes from. Read and switched over HTTP; a
   * change made elsewhere arrives as `bible_version_confirmed` on the socket.
   */
  bibleVersion: BibleVersionState

  /** Reference ID of the selected pending item, or null when nothing is selected. */
  selectedId: string | null
  selectPending: (id: string) => void
  /** Stage a history entry for a repeat display; nothing is sent. */
  selectHistory: (reference: BibleReference) => void
  /**
   * The history entry staged in Up Next, but only while it is still in the list;
   * a staged entry that has aged out stays staged and just stops being marked.
   */
  selectedHistoryId: string | null
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
  const [currentDisplay, setCurrentDisplay] = useState<BibleReference | null>(null)
  // What Up Next holds. One piece of state, so a queued selection and a manual
  // passage can never both look staged.
  const [staged, setStaged] = useState<StagedReference | null>(null)
  // True between pressing Show and the server's answer. Repeated clicks must not
  // queue the same passage twice, and a confirmation that never comes (the
  // socket dropped) must not leave Show disabled — see the status effect below.
  const [showInFlight, setShowInFlight] = useState(false)
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
   *
   * A manually staged passage and a history entry are deliberately unaffected:
   * they never enter the queue, so an arriving detection must not discard them.
   */
  const keepStagedReference = useCallback((next: BibleReference[]) => {
    setStaged(prev =>
      prev && prev.kind === 'pending' && !next.some(reference => reference.id === prev.id)
        ? null
        : prev
    )
  }, [])

  // Filled in once the hook below exists; the socket handler is declared before it.
  const applyBibleVersionRef = useRef<(data: unknown) => void>(() => {})

  const handleMessage = (message: OperatorMessage) => {
    switch (message.type) {
      case 'state_snapshot': {
        setPending(message.data.pending)
        setHistory(message.data.history)
        setPendingCount(message.data.pending_count)
        setHistoryCount(message.data.history_count)
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
        keepStagedReference(message.data.pending)
        break
      }
      case 'pending_update':
        setPending(message.data.pending)
        setPendingCount(message.data.count)
        keepStagedReference(message.data.pending)
        break
      case 'approval_confirmed': {
        const shown = message.data.reference
        // The server pushes the approved reference; fold it into history and the
        // counters so the sidebar is not stale until the next snapshot.
        setHistory(prev => [shown, ...prev].slice(0, HISTORY_WINDOW))
        setHistoryCount(count => count + 1)
        setCurrentDisplay(shown)
        setShowInFlight(false)
        pulse('approve')
        showNotice('success', `Shown: ${formatReference(shown)}`)
        break
      }
      case 'display_confirmed': {
        // The server resolved the manual passage and pushed it to the displays,
        // so this is the first moment the console can say it is on screen. The
        // reference comes back server-built: manual, with no confidence.
        const shown = message.data.reference
        setHistory(prev => [shown, ...prev].slice(0, HISTORY_WINDOW))
        setHistoryCount(count => count + 1)
        setCurrentDisplay(shown)
        setShowInFlight(false)
        // It is on screen now, so it is no longer waiting in Up Next — unless the
        // operator staged something else while this request was in flight, which
        // is why the confirmation has to name the passage it belongs to.
        setStaged(prev => (prev && isStagedPassage(prev, shown) ? null : prev))
        showNotice('success', `Shown: ${formatReference(shown)} (manual)`)
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
      case 'bible_version_confirmed':
        applyBibleVersionRef.current(message.data)
        break
      case 'error': {
        const { action, message: reason } = message.data
        console.error('Server error:', reason)
        // A rejected or failed action is over: let the operator try again.
        if (action === 'approve' || action === 'display_reference') setShowInFlight(false)
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

  const { status, send, reconnect } = useWebSocket<OperatorMessage>('/ws/operator', handleMessage, {
    // The token goes in the socket's first message, not the URL: URLs end up
    // in server and proxy logs. Always sent, even empty: with auth on and no
    // token saved, the empty message draws the server's refusal at once
    // instead of waiting out its 10 s first-message timeout. A server without
    // auth ignores it.
    firstMessage: {
      action: 'authenticate',
      token: token ?? '',
    } satisfies AuthenticateAction,
    onClose: event => {
      // WebSocket close code 1008 = policy violation (auth failure)
      if (event.code === 1008) {
        // A refusal answers the first message, before anything else: with no
        // token tried there is nothing to call failed, so the prompt opens
        // without the error text. A 1008 is only ever sent pre-auth, so the
        // socket behind it was never really connected.
        if (token !== null) {
          setAuthFailed(true)
          // Clear invalid token
          localStorage.removeItem(TOKEN_STORAGE_KEY)
          setToken(null)
        }
        setHasConnected(false)
      }
    },
  })

  // Remember a successful connection: it is what separates a dropped socket (the
  // console stays, with a reconnect banner) from a server that wants a token.
  useEffect(() => {
    if (status === 'open') setHasConnected(true)
  }, [status])

  // A show request only exists while the socket is open. If it dropped, the
  // confirmation is not coming and the staged passage is still theirs to show.
  useEffect(() => {
    if (status !== 'open') setShowInFlight(false)
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

  // The queue's Approve button approves a staged *detection*. A manual passage is
  // shown from Up Next instead, so this stays off until a queued item is staged.
  const approveSelected = useCallback(() => {
    if (staged?.kind === 'pending') {
      sendAction({ action: 'approve', reference_id: staged.id })
    }
  }, [staged, sendAction])

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

  const selectPending = useCallback((id: string) => setStaged({ kind: 'pending', id }), [])

  // Staging a history entry: NEXT previews it and nothing else changes. The
  // notice says "staged", never "shown" — nothing is on the display until the
  // server confirms the Show request.
  const selectHistory = useCallback(
    (reference: BibleReference) => {
      setStaged({ kind: 'history', reference })
      showNotice('info', `Staged: ${formatReference(reference)} — press Show to display it again`)
    },
    [showNotice]
  )

  const selectNext = useCallback(() => {
    setStaged(prev => {
      const currentId = prev?.kind === 'pending' ? prev.id : null
      if (pending.length === 0) return null
      if (currentId === null) return { kind: 'pending', id: pending[0].id }
      const index = pending.findIndex(ref => ref.id === currentId)
      if (index === -1) return { kind: 'pending', id: pending[0].id }
      return { kind: 'pending', id: pending[Math.min(index + 1, pending.length - 1)].id }
    })
  }, [pending])

  const selectPrevious = useCallback(() => {
    setStaged(prev => {
      const currentId = prev?.kind === 'pending' ? prev.id : null
      if (pending.length === 0) return null
      if (currentId === null) return { kind: 'pending', id: pending[0].id }
      const index = pending.findIndex(ref => ref.id === currentId)
      if (index === -1) return { kind: 'pending', id: pending[0].id }
      return { kind: 'pending', id: pending[Math.max(index - 1, 0)].id }
    })
  }, [pending])

  const deselect = useCallback(() => setStaged(null), [])

  // Staging is client-side and local to the console. A queued selection resolves
  // through the queue by ID, so it cannot drift from the list on screen; a manual
  // passage is a self-contained snapshot from the server's lookup.
  const selectedId = staged?.kind === 'pending' ? staged.id : null
  const selectedHistoryId =
    staged?.kind === 'history' && history.some(entry => entry.id === staged.reference.id)
      ? staged.reference.id
      : null
  const nextReference =
    staged === null
      ? null
      : staged.kind === 'manual'
        ? passageToReference(staged.passage)
        : staged.kind === 'history'
          ? staged.reference
          : (pending.find(reference => reference.id === staged.id) ?? null)
  const nextSource: NextSource =
    staged === null
      ? null
      : staged.kind === 'manual'
        ? 'manual'
        : staged.kind === 'history'
          ? 'history'
          : 'detected'

  // Show is the one place the sources diverge, and they stay separate on the
  // wire: a queued reference is approved by ID as before, while a passage the
  // operator chose — by hand or from history — is sent as coordinates for the
  // server to resolve itself. A history entry carries no ID that means anything
  // to the server (nothing is queued), so showing it again is the same request
  // the search panel makes; only the coordinates travel.
  const showNext = useCallback(() => {
    if (!staged || showInFlight) return
    setShowInFlight(true)
    if (staged.kind === 'pending') {
      sendAction({ action: 'approve', reference_id: staged.id })
      return
    }
    const coordinates = staged.kind === 'manual' ? staged.passage : staged.reference
    sendAction({
      action: 'display_reference',
      book: coordinates.book,
      chapter: coordinates.chapter,
      verse_start: coordinates.verse_start,
      verse_end: coordinates.verse_end,
    })
  }, [staged, showInFlight, sendAction])

  // Staging a lookup result: NEXT previews it and nothing else changes. The
  // notice says "staged", never "shown" — nothing is on the display until the
  // server confirms the Show request.
  const stageManualPassage = useCallback(
    (passage: Passage) => {
      setStaged({ kind: 'manual', passage })
      showNotice(
        'info',
        `Staged: ${formatReference(passageToReference(passage))} — press Show to display`
      )
    },
    [showNotice]
  )

  // Shift+Enter in Find Scripture: the resolved coordinates go to the display
  // without staging anything first. This is the same `display_reference` action
  // `showNext` sends for a staged manual passage — coordinates only, resolved
  // by the server — so no new display path is introduced. The in-flight guard
  // is shared with Show: a confirmation or rejection settles it the same way.
  const showManualDirect = useCallback(
    (request: PassageQuery) => {
      if (showInFlight) return
      setShowInFlight(true)
      sendAction({
        action: 'display_reference',
        book: request.book,
        chapter: request.chapter,
        verse_start: request.verseStart,
        verse_end: request.verseEnd,
      })
    },
    [showInFlight, sendAction]
  )

  // Manual passage search: the server's canon, the server's text, and staging on
  // success only.
  const search = useScriptureSearch(token, stageManualPassage, showManualDirect)

  const isConnected = status === 'open'

  // The pipeline's language: read once the socket is up, switched only on request.
  const spokenLanguage = useSpokenLanguage(token, isConnected, showNotice)

  const bibleVersion = useBibleVersion(token, isConnected, showNotice)
  applyBibleVersionRef.current = bibleVersion.applyConfirmed

  // Speech detection health, asked of the server only while it is reachable.
  const pipeline = usePipelineStatus(token, isConnected)

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
    currentDisplay,
    hasDisplayContent: currentDisplay !== null,
    displayConnected,
    pipeline,
    nextReference,
    nextSource,
    showNext,
    clearNext: deselect,
    canShowNext: nextReference !== null && isConnected && !showInFlight,
    showManualDirect,

    search,
    spokenLanguage,
    bibleVersion,

    selectedId,
    selectPending,
    selectHistory,
    selectedHistoryId,
    selectNext,
    selectPrevious,
    deselect,

    approveSelected,
    clearDisplay,
    clearPendingQueue,
    canApprove: staged?.kind === 'pending' && isConnected,
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
