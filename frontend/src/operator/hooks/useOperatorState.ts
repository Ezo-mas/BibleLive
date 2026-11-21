import { useCallback, useEffect, useRef, useState } from 'react'
import { useI18n } from '../../shared/i18nContext'
import { formatReference } from '../../shared/reference'
import type {
  AuthenticateAction,
  AutoMode,
  BibleReference,
  ClearMode,
  StreamOverlaySettings,
  OperatorMessage,
  Passage,
} from '../../shared/types'
import { useWebSocket } from '../../shared/useWebSocket'
import {
  coerceDisplayTheme,
  coerceVerseLimit,
  readStoredDisplayTheme,
  readStoredVerseLimit,
  writeStoredDisplayTheme,
  writeStoredVerseLimit,
} from '../../shared/verseText'
import type { DisplayTheme, VerseLimit } from '../../shared/verseText'
import { passageToReference } from '../passageApi'
import type { PassageQuery } from '../passageApi'
import { createInputLevelsStore } from '../state/inputLevelsStore'
import { createOperatorState, operatorReducer } from '../state/operatorReducer'
import type { OperatorState } from '../state/operatorHookTypes'
import type { OperatorEffect, OperatorEvent } from '../state/operatorTypes'
import { useBibleVersion } from './useBibleVersion'
import { useIdleScreen } from './useIdleScreen'
import { useNotices } from './useNotices'
import { useOperatorToken } from './useOperatorToken'
import { usePassageText } from './usePassageText'
import { usePipelineStatus } from './usePipelineStatus'
import { usePlan } from './usePlan'
import type { PlanItem } from './usePlan'
import { usePlanSnippets } from './usePlanSnippets'
import { useRowCursor } from './useRowCursor'
import { useScriptureSearch } from './useScriptureSearch'
import { useSpokenLanguage } from './useSpokenLanguage'

export type { OperatorState } from '../state/operatorHookTypes'
export type { RowCursor } from '../state/operatorTypes'
export type { FlashTarget, NoticeTone, OperatorNotice } from './useNotices'

/** Keep a second click from removing the row that moves under the pointer. */
const DISCARD_COOLDOWN_MS = 500

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
  const { token, authFailed, tokenInput, setTokenInput, submitToken, logout, onSocketClose } =
    useOperatorToken()
  const [state, setState] = useState(() =>
    createOperatorState(
      coerceVerseLimit(readStoredVerseLimit(window.localStorage)),
      coerceDisplayTheme(readStoredDisplayTheme(window.localStorage))
    )
  )
  // State advances synchronously so two intents in one React batch see each other:
  // a second Show is a no-op while the first is in flight.
  const stateRef = useRef(state)
  // Filled in once the socket and hooks below exist; dispatch is declared before them.
  const effectsRef = useRef<(effects: OperatorEffect[]) => void>(() => {})
  const dispatch = useCallback((event: OperatorEvent) => {
    const result = operatorReducer(stateRef.current, event)
    stateRef.current = result.state
    setState(result.state)
    effectsRef.current(result.effects)
  }, [])
  // Whether this session ever reached the server. A socket that opened and then
  // closed is a dropped connection, which is a different situation from a server
  // refusing us for want of a token.
  const [hasConnected, setHasConnected] = useState(false)
  const discardTimerRef = useRef<number | undefined>(undefined)
  const clearDiscardInFlight = useCallback(() => dispatch({ type: 'discard_settled' }), [dispatch])
  const { notice, flash, showNotice, showMessage, pulse, dismissNotice } = useNotices()
  const [inputLevels] = useState(createInputLevelsStore)

  const {
    status,
    send,
    reconnect: reconnectSocket,
  } = useWebSocket<OperatorMessage>('/ws/operator', onMessage, {
    maxReconnectAttempts: Infinity,
    // The token goes in the socket's first message, not the URL: URLs end up
    // in server and proxy logs. Always sent, even empty: with auth on and no
    // token saved, the empty message draws the server's refusal at once
    // instead of waiting out its 10 s first-message timeout. A server without
    // auth ignores it.
    firstMessage: { action: 'authenticate', token: token ?? '' } satisfies AuthenticateAction,
    onClose: event => {
      onSocketClose(event)
      clearDiscardInFlight()
      // A 1008 is only ever sent pre-auth, so the socket behind it was never really
      // connected: it must not count as a session that reached the server.
      if (event.code === 1008) setHasConnected(false)
    },
  })
  const isConnected = status === 'open'
  const reconnect = useCallback(() => {
    clearDiscardInFlight()
    reconnectSocket()
  }, [clearDiscardInFlight, reconnectSocket])
  // Remember a successful connection: it is what separates a dropped socket (the
  // console stays, with a reconnect banner) from a server that wants a token.
  useEffect(() => {
    if (status === 'open') setHasConnected(true)
  }, [status])
  // A show request only exists while the socket is open. If it dropped, the
  // confirmation is not coming, so Show must not stay blocked.
  useEffect(() => {
    if (status !== 'open') dispatch({ type: 'disconnected' })
  }, [status, dispatch])
  useEffect(() => () => window.clearTimeout(discardTimerRef.current), [])

  // The pipeline's language: read once the socket is up, switched only on request.
  const spokenLanguage = useSpokenLanguage(token, isConnected, showNotice)
  const bibleVersion = useBibleVersion(token, isConnected, showNotice)
  const idleScreen = useIdleScreen(token)
  // The server's answer to this console's own read or change is the newest word on it.
  const idleInfo = idleScreen.info
  useEffect(() => {
    if (idleInfo === null) return
    dispatch({
      type: 'set_idle_image',
      image: idleInfo.image ? { version: idleInfo.image.version, fit: idleInfo.fit } : null,
    })
  }, [idleInfo, dispatch])

  effectsRef.current = effects => {
    for (const effect of effects) {
      switch (effect.type) {
        case 'notice':
          showMessage(effect.tone, effect.message)
          break
        case 'flash':
          pulse(effect.target)
          break
        case 'store_verse_limit':
          writeStoredVerseLimit(effect.value, window.localStorage)
          break
        case 'store_display_theme':
          writeStoredDisplayTheme(effect.value, window.localStorage)
          break
        case 'send':
          send(effect.action)
          break
        case 'bible_version':
          bibleVersion.applyConfirmed(effect.data)
          break
        case 'log_error':
          console.error('Server error:', effect.reason)
          break
        case 'start_discard_cooldown':
          discardTimerRef.current = window.setTimeout(clearDiscardInFlight, DISCARD_COOLDOWN_MS)
          break
        case 'clear_discard_timer':
          window.clearTimeout(discardTimerRef.current)
          discardTimerRef.current = undefined
          break
        default: {
          const unhandled: never = effect
          void unhandled
        }
      }
    }
  }

  // A manual passage reaches the display one way only: its coordinates, for the
  // server to resolve itself (`display_reference`). Find Scripture, the plan, history
  // and follow-along all use it; the in-flight guard is shared with approvals, so a
  // confirmation or rejection settles it the same way.
  const showManualDirect = useCallback(
    (request: PassageQuery) => {
      if (!isConnected) return
      dispatch({
        type: 'show_direct',
        request: {
          book: request.book,
          chapter: request.chapter,
          verse_start: request.verseStart,
          verse_end: request.verseEnd,
        },
      })
    },
    [isConnected, dispatch]
  )
  // A queued detection is approved by ID, never by position.
  const approvePending = useCallback(
    (id: string) => {
      if (isConnected) dispatch({ type: 'approve_pending', id })
    },
    [isConnected, dispatch]
  )
  const showHistory = useCallback(
    (reference: BibleReference) =>
      showManualDirect({
        book: reference.book,
        chapter: reference.chapter,
        verseStart: reference.verse_start,
        verseEnd: reference.verse_end,
      }),
    [showManualDirect]
  )
  const showPlanItem = useCallback(
    (item: PlanItem) =>
      showManualDirect({
        book: item.book,
        chapter: item.chapter,
        verseStart: item.verse_start,
        verseEnd: item.verse_end,
      }),
    [showManualDirect]
  )

  const plan = usePlan()
  const addToPlan = plan.add
  const planPassage = useCallback(
    (passage: Passage) => {
      const ref = formatReference(passageToReference(passage))
      // Added: the row appears in the Plan. Already there: nothing changes on screen,
      // so that one is said.
      if (!addToPlan(passage)) showNotice('info', t('notice.alreadyPlanned', { ref }))
    },
    [addToPlan, showNotice, t]
  )
  // Search names books as the loaded Bible translation does, so it follows a switch.
  const search = useScriptureSearch(token, showManualDirect, bibleVersion.version, planPassage)
  const cursor = useRowCursor(state, dispatch, plan.items, approvePending, showPlanItem)
  const planSnippets = usePlanSnippets(plan.items, token, isConnected, bibleVersion.version)
  const { currentDisplay, followAlongHint, showInFlight } = state
  // A hint is only meaningful next to the passage it follows on from. Comparing
  // ids here, not when the hint arrives, means a display that changed or was
  // cleared simply stops matching — whichever message got here first.
  const followAlong =
    followAlongHint !== null &&
    currentDisplay !== null &&
    followAlongHint.display_id === currentDisplay.id
      ? followAlongHint
      : null
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
  // Show on a hint is the manual door, nothing newer: the same request Find
  // Scripture sends with Enter, coordinates only, re-resolved by the server.
  const showFollowAlong = useCallback(() => {
    if (!followAlong) return
    showManualDirect({
      book: followAlong.book,
      chapter: followAlong.chapter,
      verseStart: followAlong.show_from ?? followAlong.verse_start,
      verseEnd: followAlong.verse_end,
    })
  }, [followAlong, showManualDirect])
  // Speech detection health, asked of the server only while it is reachable.
  const pipeline = usePipelineStatus(token, isConnected)
  const pipelineOffline = pipeline?.status === 'offline'
  useEffect(() => {
    if (!isConnected || pipelineOffline) inputLevels.reset()
  }, [isConnected, pipelineOffline, inputLevels])
  useEffect(() => inputLevels.reset, [inputLevels])

  function onMessage(message: OperatorMessage) {
    if (message.type === 'input_levels') {
      if (!pipelineOffline) inputLevels.receive(message.data.levels)
      return
    }
    dispatch(message)
  }

  const discardPending = useCallback(
    (id: string) => {
      if (isConnected) dispatch({ type: 'discard_pending', id })
    },
    [isConnected, dispatch]
  )
  const clearDisplay = useCallback(() => dispatch({ type: 'clear_display' }), [dispatch])
  const clearPendingQueue = useCallback(() => {
    if (window.confirm(t('queue.confirmClear'))) dispatch({ type: 'clear_pending' })
  }, [dispatch, t])
  const clearHistory = useCallback(() => {
    if (window.confirm(t('history.confirmClear'))) dispatch({ type: 'clear_history' })
  }, [dispatch, t])
  // Applied optimistically so the control feels immediate; the server echoes
  // the accepted value back in display_settings_confirmed.
  const setDisplayVerseLimit = useCallback(
    (next: VerseLimit) =>
      dispatch({ type: 'set_setting', action: { action: 'set_verse_limit', verse_limit: next } }),
    [dispatch]
  )
  const setDisplayBackground = useCallback(
    (next: DisplayTheme) =>
      dispatch({ type: 'set_setting', action: { action: 'set_display_theme', theme: next } }),
    [dispatch]
  )
  const setDisplayClearMode = useCallback(
    (next: ClearMode) =>
      dispatch({ type: 'set_setting', action: { action: 'set_clear_mode', clear_mode: next } }),
    [dispatch]
  )
  const setStreamOverlay = useCallback(
    (next: StreamOverlaySettings) =>
      dispatch({
        type: 'set_setting',
        action: { action: 'set_stream_overlay', stream_overlay: next },
      }),
    [dispatch]
  )
  // Not optimistic: the switch shows only what the server confirmed (display_settings_confirmed).
  const setLive = useCallback(
    (next: boolean) => dispatch({ type: 'set_live', live: next }),
    [dispatch]
  )
  // Applied optimistically so the switch feels immediate; the server echoes the
  // accepted mode back in auto_mode_confirmed.
  const setAutoApproveMode = useCallback(
    (next: AutoMode) =>
      dispatch({ type: 'set_setting', action: { action: 'set_auto_mode', auto_mode: next } }),
    [dispatch]
  )
  // Same pattern as the auto-approve mode: applied at once, confirmed by the server.
  const setFollowAutoEnabled = useCallback(
    (next: boolean) =>
      dispatch({ type: 'set_setting', action: { action: 'set_follow_auto', follow_auto: next } }),
    [dispatch]
  )

  return {
    status,
    connectionState: isConnected
      ? 'connected'
      : status === 'connecting'
        ? 'connecting'
        : 'disconnected',
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
    submitToken,
    logout,
    isAuthenticated: token !== null,
    pending: state.pending,
    history: state.history,
    pendingCount: state.pendingCount,
    historyCount: state.historyCount,
    currentDisplay,
    seenPassage: state.seenPassage,
    hasDisplayContent: currentDisplay !== null,
    displayConnected: state.displayConnected === true,
    displayMissing: isConnected && state.displayConnected === false,
    pipeline,
    inputLevels,
    plan,
    search,
    cursor,
    showManualDirect,
    approvePending,
    showHistory,
    showPlanItem,
    followAlong,
    showFollowAlong,
    canShowFollowAlong: followAlong !== null && isConnected && !showInFlight,
    spokenLanguage,
    bibleVersion,
    idleScreen,
    planSnippets,
    followAlongText,
    discardPending,
    discardInFlight: state.discardId !== null,
    clearDisplay,
    clearPendingQueue,
    clearHistory,
    canClearDisplay: currentDisplay !== null && isConnected,
    canClearPending: state.pending.length > 0 && isConnected,
    flash,
    verseLimit: state.settings.verseLimit,
    displayTheme: state.settings.theme,
    clearMode: state.settings.clearMode,
    streamOverlay: state.settings.streamOverlay,
    idleImage: state.idleImage,
    setDisplayVerseLimit,
    setDisplayBackground,
    setDisplayClearMode,
    setStreamOverlay,
    live: state.confirmed.live,
    setLive,
    autoMode: state.settings.autoMode,
    setAutoApproveMode,
    followAuto: state.settings.followAuto,
    setFollowAutoEnabled,
  }
}
