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

/** Own the socket and perform the pure reducer's effects; server confirmations remain authoritative. */
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
  // Messages and intents can arrive in one React batch. Advance the authoritative
  // state synchronously so every transition sees the preceding confirmation.
  const stateRef = useRef(state)
  const effectsRef = useRef<(effects: OperatorEffect[]) => void>(() => {})
  const dispatch = useCallback((event: OperatorEvent) => {
    const result = operatorReducer(stateRef.current, event)
    stateRef.current = result.state
    setState(result.state)
    effectsRef.current(result.effects)
  }, [])
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
    // The token stays in the socket's first message, never its URL. Even an
    // empty token prompts an authenticated server's refusal immediately.
    firstMessage: { action: 'authenticate', token: token ?? '' } satisfies AuthenticateAction,
    onClose: event => {
      onSocketClose(event)
      clearDiscardInFlight()
      // A pre-auth refusal never counts as a session that reached the server.
      if (event.code === 1008) setHasConnected(false)
    },
  })
  const isConnected = status === 'open'
  const reconnect = useCallback(() => {
    clearDiscardInFlight()
    reconnectSocket()
  }, [clearDiscardInFlight, reconnectSocket])
  useEffect(() => {
    if (status === 'open') setHasConnected(true)
  }, [status])
  useEffect(() => {
    if (status !== 'open') dispatch({ type: 'disconnected' })
  }, [status, dispatch])
  useEffect(() => () => window.clearTimeout(discardTimerRef.current), [])

  const spokenLanguage = useSpokenLanguage(token, isConnected, showNotice)
  const bibleVersion = useBibleVersion(token, isConnected, showNotice)
  const idleScreen = useIdleScreen(token)
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
  const planSnippets = usePlanSnippets(staging.plan.items, token, isConnected)
  const { currentDisplay, followAlongHint, showInFlight } = state
  const followAlong =
    followAlongHint !== null &&
    currentDisplay !== null &&
    followAlongHint.display_id === currentDisplay.id
      ? followAlongHint
      : null
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
  const showFollowAlong = useCallback(() => {
    if (!followAlong) return
    showManualDirect({
      book: followAlong.book,
      chapter: followAlong.chapter,
      verseStart: followAlong.verse_start,
      verseEnd: followAlong.verse_end,
    })
  }, [followAlong, showManualDirect])
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
  const setAutoApproveMode = useCallback(
    (next: AutoMode) =>
      dispatch({ type: 'set_setting', action: { action: 'set_auto_mode', auto_mode: next } }),
    [dispatch]
  )
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
    // Once connected, a dropped socket keeps the console and reconnect banner.
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
    autoMode: state.settings.autoMode,
    setAutoApproveMode,
    followAuto: state.settings.followAuto,
    setFollowAutoEnabled,
  }
}
