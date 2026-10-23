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
import { readIdleImage } from '../../shared/idleScreen'
import type { IdleImage } from '../../shared/idleScreen'
import { formatReference } from '../../shared/reference'
import type {
  AuthenticateAction,
  AutoMode,
  BibleReference,
  FollowAlongHint,
  OperatorAction,
  OperatorMessage,
  Passage,
  PipelineStatus,
} from '../../shared/types'
import { passageToReference } from '../passageApi'
import type { PassageQuery } from '../passageApi'
import { dedupeHistory, samePassage } from '../passageKey'
import { usePlan } from './usePlan'
import type { PlanItem, PlanState } from './usePlan'
import { usePlanSnippets } from './usePlanSnippets'
import { usePassageText } from './usePassageText'
import { autoModeOption, coerceAutoMode } from '../autoMode'
import { useScriptureSearch } from './useScriptureSearch'
import { usePipelineStatus } from './usePipelineStatus'
import type { ScriptureSearchState } from './useScriptureSearch'
import { useSpokenLanguage } from './useSpokenLanguage'
import { useBibleVersion } from './useBibleVersion'
import type { BibleVersionState } from './useBibleVersion'
import { useIdleScreen } from './useIdleScreen'
import type { IdleScreenState } from './useIdleScreen'
import type { SpokenLanguageState } from './useSpokenLanguage'
import type { ConnectionState } from '../../components'
import { useI18n } from '../../shared/i18nContext'
import type { MessageKey } from '../../shared/i18n'
import {
  OPERATOR_TOKEN_STORAGE_KEY,
  readStoredOperatorToken,
  takeOperatorTokenFragment,
} from '../../shared/operatorToken'

/** How long a click counts as the start of a double-click, for restoring Up Next. */
const DOUBLE_CLICK_MS = 500

/** How long the button "pressed" feedback lasts after a confirmation. */
const FLASH_MS = 150

/** How long a notice stays up before it removes itself. */
const NOTICE_MS: Record<NoticeTone, number> = { success: 3000, info: 4000, error: 6000 }

/** Local storage key for operator token */
const TOKEN_STORAGE_KEY = OPERATOR_TOKEN_STORAGE_KEY

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
const ERROR_PREFIX_KEYS: Record<string, MessageKey> = {
  approve: 'notice.errApprove',
  display_reference: 'notice.errDisplayReference',
  clear: 'notice.errClear',
  clear_pending: 'notice.errClearPending',
  set_verse_limit: 'notice.errVerseLimit',
  set_display_theme: 'notice.errTheme',
  set_auto_mode: 'notice.errAutoMode',
  set_follow_auto: 'notice.errFollowAuto',
}

/**
 * Actions the console applies optimistically. When the server rejects one, the
 * control has to go back to the last confirmed value or it would keep claiming
 * a setting the display is not using.
 */
const OPTIMISTIC_ACTIONS = new Set([
  'set_verse_limit',
  'set_display_theme',
  'set_auto_mode',
  'set_follow_auto',
])

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
function isStagedPassage(staged: StagedReference, shown: BibleReference): boolean {
  if (staged.kind === 'pending') return false
  return samePassage(staged.kind === 'manual' ? staged.passage : staged.reference, shown)
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
   * coordinates only, for the server to resolve — used by Enter in Find
   * Scripture. Nothing is staged and Up Next is untouched.
   */
  showManualDirect: (request: PassageQuery) => void

  /**
   * Follow-along: the verses the speaker seems to be reading on to, or null. Only
   * set while it still follows on from the passage on screen — a hint for an
   * earlier display (changed or cleared since) is never offered.
   */
  followAlong: FollowAlongHint | null
  /**
   * Show the hinted passage: the same `display_reference` request a manual
   * search sends, coordinates only. The hint goes away when the server confirms
   * the new display, and stays if the request is refused so the operator can retry.
   */
  showFollowAlong: () => void
  canShowFollowAlong: boolean

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

  /** The picture shown while the display is idle. Read and changed over HTTP. */
  idleScreen: IdleScreenState

  /** Reference ID of the selected pending item, or null when nothing is selected. */
  selectedId: string | null
  selectPending: (id: string) => void
  /** Stage a history entry for a repeat display; nothing is sent. */
  selectHistory: (reference: BibleReference) => void
  /** Show a history entry again at once (double-click), skipping Up Next. */
  showHistory: (reference: BibleReference) => void
  /** Approve a queued detection at once (double-click), skipping Up Next. */
  approvePending: (id: string) => void

  /** Passages lined up in advance, kept in this browser. */
  plan: PlanState
  /** Looked-up text for plan items saved without any, by item id. */
  planSnippets: Record<string, string>
  /** The text the follow-along hint points at, once looked up; null until then. */
  followAlongText: string | null
  /** Stage a planned passage in Up Next (looked up fresh); nothing is sent to the display. */
  stagePlanItem: (item: PlanItem) => void
  /** Show a planned passage at once (double-click), skipping Up Next. */
  showPlanItem: (item: PlanItem) => void
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
  /** The picture the display shows while idle, so the preview can show it too; null for blank. */
  idleImage: IdleImage | null
  setDisplayVerseLimit: (next: VerseLimit) => void
  setDisplayBackground: (next: DisplayTheme) => void

  /** Which new detections the server displays without waiting for approval. */
  autoMode: AutoMode
  setAutoApproveMode: (next: AutoMode) => void

  /**
   * Follow-along auto-show: whether the server shows the verses a speaker reads on to
   * by itself. Server-held, so every console shows the same value.
   */
  followAuto: boolean
  setFollowAutoEnabled: (next: boolean) => void
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
  const { t } = useI18n()
  const [token, setToken] = useState<string | null>(() => {
    // A console the launcher opened carries its token in the fragment. It is saved
    // the same way a typed token is (handleTokenSubmit), so a refusal and the rest
    // of the flow are identical. This runs during the first render, before the
    // I18nProvider's mount-time language request reads storage.
    const fromLink = takeOperatorTokenFragment()
    if (fromLink === null) return readStoredOperatorToken()
    try {
      localStorage.setItem(TOKEN_STORAGE_KEY, fromLink)
    } catch {
      // Storage is blocked (private browsing): the link's token still works on this page.
    }
    return fromLink
  })
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
  const stagedRef = useRef<StagedReference | null>(null)
  stagedRef.current = staged
  // A double-click is click, click, double-click, and each click stages its row. The
  // double-click is meant to skip staging, so it puts back what Up Next held before
  // the gesture began instead of leaving it emptied by the row it just showed.
  const gestureRef = useRef<{ key: string; before: StagedReference | null; at: number } | null>(
    null
  )
  const beginGesture = useCallback((key: string) => {
    const now = Date.now()
    const current = gestureRef.current
    if (current && current.key === key && now - current.at < DOUBLE_CLICK_MS) return
    gestureRef.current = { key, before: stagedRef.current, at: now }
  }, [])
  const restoreBeforeGesture = useCallback((key: string) => {
    const current = gestureRef.current
    gestureRef.current = null
    if (current && current.key === key && Date.now() - current.at < DOUBLE_CLICK_MS * 2) {
      setStaged(current.before)
    }
  }, [])

  // True between pressing Show and the server's answer. Repeated clicks must not
  // queue the same passage twice, and a confirmation that never comes (the
  // socket dropped) must not leave Show disabled — see the status effect below.
  const [showInFlight, setShowInFlight] = useState(false)
  // The latest follow-along hint. Transient: never in a snapshot, and only ever
  // offered while its `display_id` is still the passage on screen.
  const [followAlongHint, setFollowAlongHint] = useState<FollowAlongHint | null>(null)
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
  // The display's idle picture, from the snapshot, settings echoes and this console's
  // own idle-screen requests. The server tells only displays when another console
  // changes it, so that change shows here after the next snapshot or echo.
  const [idleImage, setIdleImage] = useState<IdleImage | null>(null)
  // Auto-approve mode. Server-side state (persisted there too), so the console
  // only mirrors it: the snapshot on connect is the source of truth.
  const [autoMode, setAutoMode] = useState<AutoMode>('off')
  // Follow-along auto-show, mirrored the same way: the server's value wins.
  const [followAuto, setFollowAuto] = useState(false)
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
  const confirmedRef = useRef({ verseLimit, theme: displayTheme, autoMode, followAuto: false })

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
        setHistory(dedupeHistory(message.data.history))
        setPendingCount(message.data.pending_count)
        setHistoryCount(message.data.history_count)
        setCurrentDisplay(message.data.current_display)
        // Hints are not part of a snapshot; one from before a reconnect is stale.
        setFollowAlongHint(null)
        setDisplayConnected(message.data.display_connections > 0)
        if (message.data.display_settings) {
          const verseLimit = coerceVerseLimit(message.data.display_settings.verse_limit)
          const theme = coerceDisplayTheme(message.data.display_settings.theme)
          setVerseLimit(verseLimit)
          setDisplayTheme(theme)
          setIdleImage(readIdleImage(message.data.display_settings))
          confirmedRef.current.verseLimit = verseLimit
          confirmedRef.current.theme = theme
        }
        // Absent when talking to an older server, which leaves the mode off.
        if (message.data.auto_mode) {
          const autoMode = coerceAutoMode(message.data.auto_mode)
          setAutoMode(autoMode)
          confirmedRef.current.autoMode = autoMode
        }
        // Absent when talking to an older server, which leaves follow-along auto-show off.
        if (typeof message.data.follow_auto === 'boolean') {
          setFollowAuto(message.data.follow_auto)
          confirmedRef.current.followAuto = message.data.follow_auto
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
        setHistory(prev => dedupeHistory([shown, ...prev]).slice(0, HISTORY_WINDOW))
        setHistoryCount(count => count + 1)
        setCurrentDisplay(shown)
        setShowInFlight(false)
        pulse('approve')
        showNotice('success', t('notice.shown', { ref: formatReference(shown) }))
        break
      }
      case 'display_confirmed': {
        // The server resolved the manual passage and pushed it to the displays,
        // so this is the first moment the console can say it is on screen. The
        // reference comes back server-built: manual, with no confidence.
        const shown = message.data.reference
        setHistory(prev => dedupeHistory([shown, ...prev]).slice(0, HISTORY_WINDOW))
        setHistoryCount(count => count + 1)
        setCurrentDisplay(shown)
        // A passage follow-along showed by itself answers no Show of this console:
        // the operator's own request stays in flight and Up Next stays as it is.
        const followed = shown.source === 'follow'
        if (!followed) {
          setShowInFlight(false)
          // It is on screen now, so it is no longer waiting in Up Next — unless the
          // operator staged something else while this request was in flight, which
          // is why the confirmation has to name the passage it belongs to.
          setStaged(prev => (prev && isStagedPassage(prev, shown) ? null : prev))
        }
        // A passage follow-along showed by itself is not one the operator picked.
        const noticeKey = followed ? 'notice.shownFollow' : 'notice.shownManual'
        showNotice('success', t(noticeKey, { ref: formatReference(shown) }))
        break
      }
      case 'follow_along':
        // Replaces any earlier hint; whether it still applies is decided against
        // the display at render time, so arrival order cannot matter.
        setFollowAlongHint(message.data)
        break
      case 'clear_confirmed':
        setCurrentDisplay(null)
        setFollowAlongHint(null)
        pulse('clear')
        showNotice('info', t('notice.displayCleared'))
        break
      case 'pending_cleared':
        pulse('clearPending')
        showNotice('info', t('notice.queueCleared'))
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
          changes.push(
            t('notice.changeVerses', {
              value: verseLimit === 'all' ? t('settings.versesAll') : verseLimit,
            })
          )
        }
        if (theme !== confirmedRef.current.theme) {
          changes.push(
            t('notice.changeBackground', {
              value: theme === 'dark' ? t('settings.bgDark') : t('settings.bgLight'),
            })
          )
        }
        setVerseLimit(verseLimit)
        setDisplayTheme(theme)
        setIdleImage(readIdleImage(message.data))
        confirmedRef.current.verseLimit = verseLimit
        confirmedRef.current.theme = theme
        if (changes.length > 0)
          showNotice('info', t('notice.saved', { changes: changes.join(', ') }))
        break
      }
      case 'auto_mode_confirmed': {
        const autoMode = coerceAutoMode(message.data.auto_mode)
        const changed = autoMode !== confirmedRef.current.autoMode
        setAutoMode(autoMode)
        confirmedRef.current.autoMode = autoMode
        if (changed) {
          showNotice(
            'info',
            t('notice.autoApprove', { mode: t(autoModeOption(autoMode).labelKey) })
          )
        }
        break
      }
      case 'follow_auto_confirmed': {
        const enabled = message.data.follow_auto
        const changed = enabled !== confirmedRef.current.followAuto
        setFollowAuto(enabled)
        confirmedRef.current.followAuto = enabled
        if (changed) {
          showNotice(
            'info',
            t('notice.followAuto', { state: t(enabled ? 'follow.on' : 'follow.off') })
          )
        }
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
        const prefix = t(ERROR_PREFIX_KEYS[action] ?? 'notice.errGeneric')
        showNotice(
          'error',
          OPTIMISTIC_ACTIONS.has(action)
            ? t('notice.reverted', { prefix, reason })
            : t('notice.failed', { prefix, reason })
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
        } else if (action === 'set_follow_auto') {
          setFollowAuto(confirmedRef.current.followAuto)
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
    if (window.confirm(t('queue.confirmClear'))) {
      sendAction({ action: 'clear_pending' })
    }
  }, [sendAction, t])

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

  // Same pattern as the auto-approve mode: applied at once, confirmed by the server.
  const setFollowAutoEnabled = useCallback(
    (next: boolean) => {
      setFollowAuto(next)
      sendAction({ action: 'set_follow_auto', follow_auto: next })
    },
    [sendAction]
  )

  const selectPending = useCallback(
    (id: string) => {
      beginGesture(`pending:${id}`)
      setStaged({ kind: 'pending', id })
    },
    [beginGesture]
  )

  // Staging a history entry: NEXT previews it and nothing else changes. The
  // notice says "staged", never "shown" — nothing is on the display until the
  // server confirms the Show request.
  const selectHistory = useCallback(
    (reference: BibleReference) => {
      beginGesture(`history:${reference.id}`)
      setStaged({ kind: 'history', reference })
      showNotice('info', t('notice.stagedAgain', { ref: formatReference(reference) }))
    },
    [beginGesture, showNotice, t]
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
  // History holds one entry per passage, so the staged one is found by its passage:
  // showing it again replaces the entry it was staged from with a new ID.
  const selectedHistoryId =
    staged?.kind === 'history'
      ? (history.find(entry => samePassage(entry, staged.reference))?.id ?? null)
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
      showNotice('info', t('notice.staged', { ref: formatReference(passageToReference(passage)) }))
    },
    [showNotice, t]
  )

  // Enter in Find Scripture: the resolved coordinates go to the display
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

  // Double-click on a history entry: the same coordinates-only request Show sends
  // for a staged one, without staging first.
  const showHistory = useCallback(
    (reference: BibleReference) => {
      if (status !== 'open') return
      restoreBeforeGesture(`history:${reference.id}`)
      showManualDirect({
        book: reference.book,
        chapter: reference.chapter,
        verseStart: reference.verse_start,
        verseEnd: reference.verse_end,
      })
    },
    [status, restoreBeforeGesture, showManualDirect]
  )

  // Double-click on a queued detection: approved by ID exactly as Show approves
  // a staged one. Shares Show's in-flight guard, so a double-click is one request.
  const approvePending = useCallback(
    (id: string) => {
      if (status !== 'open' || showInFlight) return
      restoreBeforeGesture(`pending:${id}`)
      setShowInFlight(true)
      sendAction({ action: 'approve', reference_id: id })
    },
    [status, showInFlight, restoreBeforeGesture, sendAction]
  )

  // A hint is only meaningful next to the passage it follows on from. Comparing
  // ids here, not when the hint arrives, means a display that changed or was
  // cleared simply stops matching — whichever message got here first.
  const followAlong =
    followAlongHint !== null &&
    currentDisplay !== null &&
    followAlongHint.display_id === currentDisplay.id
      ? followAlongHint
      : null

  // Show on a hint is the manual door, nothing newer: the same request Find
  // Scripture sends with Enter, coordinates only, re-resolved by the server.
  const showFollowAlong = useCallback(() => {
    if (!followAlong) return
    showManualDirect({
      book: followAlong.book,
      chapter: followAlong.chapter,
      verseStart: followAlong.verse_start,
      verseEnd: followAlong.verse_end,
    })
  }, [followAlong, showManualDirect])

  // Manual passage search: the server's canon, the server's text, and staging on
  // success only.
  const isConnected = status === 'open'

  // The pipeline's language: read once the socket is up, switched only on request.
  const spokenLanguage = useSpokenLanguage(token, isConnected, showNotice)

  const bibleVersion = useBibleVersion(token, isConnected, showNotice)
  const idleScreen = useIdleScreen(token)
  // The server's answer to this console's own read or change is the newest word on it.
  const idleInfo = idleScreen.info
  useEffect(() => {
    if (idleInfo === null) return
    setIdleImage(idleInfo.image ? { version: idleInfo.image.version, fit: idleInfo.fit } : null)
  }, [idleInfo])
  applyBibleVersionRef.current = bibleVersion.applyConfirmed

  const plan = usePlan()
  const planSnippets = usePlanSnippets(plan.items, token, isConnected)
  // The words of the passage the follow-along card points at, for its preview line.
  const followAlongText = usePassageText(
    followAlong
      ? {
          book: followAlong.book,
          chapter: followAlong.chapter,
          verseStart: followAlong.verse_start,
          verseEnd: followAlong.verse_end,
        }
      : null,
    token,
    isConnected
  )
  const addToPlan = plan.add
  const planPassage = useCallback(
    (passage: Passage) => {
      const ref = formatReference(passageToReference(passage))
      if (addToPlan(passage)) showNotice('success', t('notice.planned', { ref }))
      else showNotice('info', t('notice.alreadyPlanned', { ref }))
    },
    [addToPlan, showNotice, t]
  )

  // Search names books as the loaded Bible translation does, so it follows a switch.
  const search = useScriptureSearch(
    token,
    stageManualPassage,
    showManualDirect,
    bibleVersion.version,
    planPassage,
    // A plan row is clicked outside the search box, so its failure is a notice.
    message => showNotice('error', message)
  )

  const { stagePassage, cancelLookup } = search
  const stagePlanItem = useCallback(
    (item: PlanItem) => {
      beginGesture(`plan:${item.id}`)
      stagePassage(
        {
          book: item.book,
          chapter: item.chapter,
          verseStart: item.verse_start,
          verseEnd: item.verse_end,
        },
        true
      )
    },
    [beginGesture, stagePassage]
  )
  // The click that began a double-click has a lookup in flight; letting it land
  // after the display would stage a passage that is already on screen.
  const showPlanItem = useCallback(
    (item: PlanItem) => {
      if (status !== 'open') return
      cancelLookup()
      restoreBeforeGesture(`plan:${item.id}`)
      showManualDirect({
        book: item.book,
        chapter: item.chapter,
        verseStart: item.verse_start,
        verseEnd: item.verse_end,
      })
    },
    [status, cancelLookup, restoreBeforeGesture, showManualDirect]
  )

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

    followAlong,
    showFollowAlong,
    canShowFollowAlong: followAlong !== null && isConnected && !showInFlight,

    search,
    spokenLanguage,
    bibleVersion,
    idleScreen,

    selectedId,
    selectPending,
    selectHistory,
    showHistory,
    approvePending,
    plan,
    planSnippets,
    followAlongText,
    stagePlanItem,
    showPlanItem,
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
    idleImage,
    setDisplayVerseLimit,
    setDisplayBackground,

    autoMode,
    setAutoApproveMode,

    followAuto,
    setFollowAutoEnabled,
  }
}
