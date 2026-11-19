import { useCallback, useEffect, useRef, useState } from 'react'
import { useI18n } from '../../shared/i18nContext'
import type { AuthenticateAction, AutoMode, ClearMode, OperatorMessage } from '../../shared/types'
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
import { createOperatorState, operatorReducer } from '../state/operatorReducer'
import type { OperatorState } from '../state/operatorHookTypes'
import type { OperatorEffect, OperatorEvent } from '../state/operatorTypes'
import { useBibleVersion } from './useBibleVersion'
import { useIdleScreen } from './useIdleScreen'
import { useNotices } from './useNotices'
import { useOperatorToken } from './useOperatorToken'
import { usePassageText } from './usePassageText'
import { usePipelineStatus } from './usePipelineStatus'
import { usePlanSnippets } from './usePlanSnippets'
import { useSpokenLanguage } from './useSpokenLanguage'
import { useStaging } from './useStaging'

export type { OperatorState } from '../state/operatorHookTypes'
export type { StagedReference } from '../state/operatorTypes'
export type { FlashTarget, NoticeTone, OperatorNotice } from './useNotices'
export type { NextSource } from './useStaging'

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
  // a second Show is a no-op; staging then Show approves the newly staged item.
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

  const {
    status,
    send,
    reconnect: reconnectSocket,
  } = useWebSocket<OperatorMessage>('/ws/operator', dispatch, {
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
  // confirmation is not coming and the staged passage is still theirs to show.
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

  const staging = useStaging(state, dispatch, token, isConnected, bibleVersion.version, showNotice)
  const planSnippets = usePlanSnippets(staging.plan.items, token, isConnected, bibleVersion.version)
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
  const { showManualDirect } = staging
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

  const approveSelected = useCallback(() => dispatch({ type: 'approve_selected' }), [dispatch])
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
    displayConnected: state.displayConnected,
    pipeline,
    ...staging,
    clearNext: staging.deselect,
    canShowNext: staging.nextReference !== null && isConnected && !showInFlight,
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
    approveSelected,
    clearDisplay,
    clearPendingQueue,
    canApprove: state.staged?.kind === 'pending' && isConnected,
    canClearDisplay: currentDisplay !== null && isConnected,
    canClearPending: state.pending.length > 0 && isConnected,
    flash,
    verseLimit: state.settings.verseLimit,
    displayTheme: state.settings.theme,
    clearMode: state.settings.clearMode,
    idleImage: state.idleImage,
    setDisplayVerseLimit,
    setDisplayBackground,
    setDisplayClearMode,
    live: state.confirmed.live,
    setLive,
    autoMode: state.settings.autoMode,
    setAutoApproveMode,
    followAuto: state.settings.followAuto,
    setFollowAutoEnabled,
  }
}
