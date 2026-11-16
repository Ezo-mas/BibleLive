import { coerceClearMode } from '../../shared/clearMode'
import { readIdleImage } from '../../shared/idleScreen'
import type { MessageKey } from '../../shared/i18n'
import { formatReference } from '../../shared/reference'
import type { BibleReference, DisplaySettings } from '../../shared/types'
import { coerceDisplayTheme, coerceVerseLimit } from '../../shared/verseText'
import { autoModeOption, coerceAutoMode } from '../autoMode'
import { dedupeHistory, samePassage } from '../passageKey'
import type {
  NoticeMessage,
  OperatorEffect,
  OperatorEvent,
  OperatorReducerState,
  OperatorSettings,
  OperatorTransition,
  OptimisticSettingAction,
  StagedReference,
} from './operatorTypes'

const HISTORY_WINDOW = 20

const ERROR_PREFIX_KEYS: Record<string, MessageKey> = {
  approve: 'notice.errApprove',
  display_reference: 'notice.errDisplayReference',
  clear: 'notice.errClear',
  clear_pending: 'notice.errClearPending',
  discard_pending: 'notice.errDiscardPending',
  set_verse_limit: 'notice.errVerseLimit',
  set_display_theme: 'notice.errTheme',
  set_clear_mode: 'notice.errClearMode',
  set_auto_mode: 'notice.errAutoMode',
  set_follow_auto: 'notice.errFollowAuto',
}

const SETTING_KEYS = {
  set_verse_limit: 'verseLimit',
  set_display_theme: 'theme',
  set_clear_mode: 'clearMode',
  set_auto_mode: 'autoMode',
  set_follow_auto: 'followAuto',
} as const satisfies Record<OptimisticSettingAction['action'], keyof OperatorSettings>

function isOptimisticAction(action: string): action is keyof typeof SETTING_KEYS {
  return Object.prototype.hasOwnProperty.call(SETTING_KEYS, action)
}

/** Stored browser preferences are read by the hook and supplied explicitly. */
export function createOperatorState(
  verseLimit: OperatorSettings['verseLimit'] = 3,
  theme: OperatorSettings['theme'] = 'dark'
): OperatorReducerState {
  const settings: OperatorSettings = {
    verseLimit,
    theme,
    clearMode: 'idle',
    autoMode: 'off',
    followAuto: false,
  }
  return {
    pending: [],
    history: [],
    pendingCount: 0,
    historyCount: 0,
    currentDisplay: null,
    seenPassage: false,
    staged: null,
    showInFlight: false,
    discardId: null,
    discardCoolingDown: false,
    followAlongHint: null,
    displayConnected: false,
    settings,
    confirmed: { ...settings },
    idleImage: null,
  }
}

function keepStagedReference(staged: StagedReference | null, pending: BibleReference[]) {
  return staged?.kind === 'pending' && !pending.some(reference => reference.id === staged.id)
    ? null
    : staged
}

function isStagedPassage(staged: StagedReference, shown: BibleReference): boolean {
  if (staged.kind === 'pending') return false
  return samePassage(staged.kind === 'manual' ? staged.passage : staged.reference, shown)
}

function displaySettings(data: DisplaySettings) {
  return {
    verseLimit: coerceVerseLimit(data.verse_limit),
    theme: coerceDisplayTheme(data.theme),
    clearMode: coerceClearMode(data.clear_mode),
  }
}

function storeSetting(settings: OperatorSettings, action: string): OperatorEffect[] {
  if (action === 'set_verse_limit')
    return [{ type: 'store_verse_limit', value: settings.verseLimit }]
  if (action === 'set_display_theme')
    return [{ type: 'store_display_theme', value: settings.theme }]
  return []
}

/** Pure transitions only: no socket, browser storage, clock, or timer access. */
export function operatorReducer(
  state: OperatorReducerState,
  event: OperatorEvent
): OperatorTransition {
  const effects: OperatorEffect[] = []
  const notice = (tone: 'success' | 'info' | 'error', message: NoticeMessage) =>
    effects.push({ type: 'notice', tone, message })
  let next = state

  switch (event.type) {
    case 'state_snapshot': {
      const data = event.data
      const settings = { ...state.settings }
      const confirmed = { ...state.confirmed }
      if (data.display_settings) {
        Object.assign(settings, displaySettings(data.display_settings))
        Object.assign(confirmed, displaySettings(data.display_settings))
      }
      if (data.auto_mode) settings.autoMode = confirmed.autoMode = coerceAutoMode(data.auto_mode)
      if (typeof data.follow_auto === 'boolean')
        settings.followAuto = confirmed.followAuto = data.follow_auto
      next = {
        ...state,
        pending: data.pending,
        history: dedupeHistory(data.history),
        pendingCount: data.pending_count,
        historyCount: data.history_count,
        currentDisplay: data.current_display,
        seenPassage: state.seenPassage || data.current_display !== null,
        followAlongHint: null,
        displayConnected: data.display_connections > 0,
        settings,
        confirmed,
        idleImage: data.display_settings ? readIdleImage(data.display_settings) : state.idleImage,
        staged: keepStagedReference(state.staged, data.pending),
      }
      break
    }
    case 'pending_update': {
      const startCooldown =
        state.discardId !== null &&
        !state.discardCoolingDown &&
        !event.data.pending.some(reference => reference.id === state.discardId)
      next = {
        ...state,
        pending: event.data.pending,
        pendingCount: event.data.count,
        staged: keepStagedReference(state.staged, event.data.pending),
        discardCoolingDown: state.discardCoolingDown || startCooldown,
      }
      if (startCooldown) effects.push({ type: 'start_discard_cooldown' })
      break
    }
    case 'approval_confirmed':
    case 'display_confirmed': {
      const shown = event.data.reference
      const followed = event.type === 'display_confirmed' && shown.source === 'follow'
      next = {
        ...state,
        history: dedupeHistory([shown, ...state.history]).slice(0, HISTORY_WINDOW),
        historyCount: state.historyCount + 1,
        currentDisplay: shown,
        seenPassage: true,
        showInFlight: followed ? state.showInFlight : false,
        staged:
          event.type === 'display_confirmed' &&
          !followed &&
          state.staged &&
          isStagedPassage(state.staged, shown)
            ? null
            : state.staged,
      }
      if (event.type === 'approval_confirmed') effects.push({ type: 'flash', target: 'approve' })
      notice('success', {
        key:
          event.type === 'approval_confirmed'
            ? 'notice.shown'
            : followed
              ? 'notice.shownFollow'
              : 'notice.shownManual',
        params: { ref: formatReference(shown) },
      })
      break
    }
    case 'follow_along':
      next = { ...state, followAlongHint: event.data }
      break
    case 'clear_confirmed':
      next = { ...state, currentDisplay: null, followAlongHint: null }
      effects.push({ type: 'flash', target: 'clear' })
      notice('info', { key: 'notice.displayCleared' })
      break
    case 'pending_cleared':
      effects.push({ type: 'flash', target: 'clearPending' })
      notice('info', { key: 'notice.queueCleared' })
      break
    case 'connection_status':
      next = { ...state, displayConnected: event.data.display > 0 }
      break
    case 'display_settings_confirmed': {
      const settings = displaySettings(event.data)
      const changes: NoticeMessage[] = []
      if (settings.verseLimit !== state.confirmed.verseLimit)
        changes.push({
          key: 'notice.changeVerses',
          params: {
            value:
              settings.verseLimit === 'all' ? { key: 'settings.versesAll' } : settings.verseLimit,
          },
        })
      if (settings.theme !== state.confirmed.theme)
        changes.push({
          key: 'notice.changeBackground',
          params: {
            value: { key: settings.theme === 'dark' ? 'settings.bgDark' : 'settings.bgLight' },
          },
        })
      if (settings.clearMode !== state.confirmed.clearMode)
        changes.push({
          key: 'notice.changeClearMode',
          params: {
            value: {
              key: settings.clearMode === 'idle' ? 'settings.clearIdle' : 'settings.clearBlank',
            },
          },
        })
      next = {
        ...state,
        settings: { ...state.settings, ...settings },
        confirmed: { ...state.confirmed, ...settings },
        idleImage: readIdleImage(event.data),
      }
      if (changes.length > 0)
        notice('info', { key: 'notice.saved', params: { changes: { join: changes } } })
      break
    }
    case 'auto_mode_confirmed': {
      const autoMode = coerceAutoMode(event.data.auto_mode)
      next = {
        ...state,
        settings: { ...state.settings, autoMode },
        confirmed: { ...state.confirmed, autoMode },
      }
      if (autoMode !== state.confirmed.autoMode)
        notice('info', {
          key: 'notice.autoApprove',
          params: { mode: { key: autoModeOption(autoMode).labelKey } },
        })
      break
    }
    case 'follow_auto_confirmed': {
      const followAuto = event.data.follow_auto
      next = {
        ...state,
        settings: { ...state.settings, followAuto },
        confirmed: { ...state.confirmed, followAuto },
      }
      if (followAuto !== state.confirmed.followAuto)
        notice('info', {
          key: 'notice.followAuto',
          params: { state: { key: followAuto ? 'follow.on' : 'follow.off' } },
        })
      break
    }
    case 'bible_version_confirmed':
      effects.push({ type: 'bible_version', data: event.data })
      break
    case 'error': {
      const { action: echoed, message: reason } = event.data
      const action = typeof echoed === 'string' ? echoed : ''
      const optimistic = isOptimisticAction(action)
      effects.push({ type: 'log_error', reason })
      if (action === 'discard_pending') {
        next = { ...next, discardId: null, discardCoolingDown: false }
        effects.push({ type: 'clear_discard_timer' })
      }
      if (action === 'approve' || action === 'display_reference')
        next = { ...next, showInFlight: false }
      notice('error', {
        key: optimistic ? 'notice.reverted' : 'notice.failed',
        params: {
          prefix: {
            key: Object.prototype.hasOwnProperty.call(ERROR_PREFIX_KEYS, action)
              ? ERROR_PREFIX_KEYS[action]
              : 'notice.errGeneric',
          },
          reason,
        },
      })
      if (optimistic) {
        const key = SETTING_KEYS[action]
        next = { ...next, settings: { ...state.settings, [key]: state.confirmed[key] } }
        effects.push(...storeSetting(next.settings, action))
      }
      break
    }
    case 'stage':
      next = { ...state, staged: event.staged }
      break
    case 'select_by': {
      const id = state.staged?.kind === 'pending' ? state.staged.id : null
      const index = state.pending.findIndex(reference => reference.id === id)
      const target =
        index === -1 ? 0 : Math.min(Math.max(index + event.step, 0), state.pending.length - 1)
      next = {
        ...state,
        staged:
          state.pending.length === 0 ? null : { kind: 'pending', id: state.pending[target].id },
      }
      break
    }
    case 'show_next': {
      if (!state.staged || state.showInFlight) break
      next = { ...state, showInFlight: true }
      if (state.staged.kind === 'pending') {
        effects.push({ type: 'send', action: { action: 'approve', reference_id: state.staged.id } })
      } else {
        const reference =
          state.staged.kind === 'manual' ? state.staged.passage : state.staged.reference
        effects.push({
          type: 'send',
          action: {
            action: 'display_reference',
            book: reference.book,
            chapter: reference.chapter,
            verse_start: reference.verse_start,
            verse_end: reference.verse_end,
          },
        })
      }
      break
    }
    case 'show_direct':
      if (!state.showInFlight) {
        next = { ...state, showInFlight: true }
        effects.push({ type: 'send', action: { action: 'display_reference', ...event.request } })
      }
      break
    case 'approve_selected':
      if (state.staged?.kind === 'pending')
        effects.push({ type: 'send', action: { action: 'approve', reference_id: state.staged.id } })
      break
    case 'approve_pending':
      if (!state.showInFlight) {
        next = { ...state, showInFlight: true }
        effects.push({ type: 'send', action: { action: 'approve', reference_id: event.id } })
      }
      break
    case 'discard_pending':
      if (state.discardId === null) {
        next = { ...state, discardId: event.id, discardCoolingDown: false }
        effects.push(
          { type: 'clear_discard_timer' },
          { type: 'send', action: { action: 'discard_pending', reference_id: event.id } }
        )
      }
      break
    case 'clear_display':
      effects.push({ type: 'send', action: { action: 'clear' } })
      break
    case 'clear_pending':
      effects.push({ type: 'send', action: { action: 'clear_pending' } })
      break
    case 'set_setting': {
      const action = event.action
      const settings = { ...state.settings }
      switch (action.action) {
        case 'set_verse_limit':
          settings.verseLimit = action.verse_limit
          break
        case 'set_display_theme':
          settings.theme = action.theme
          break
        case 'set_clear_mode':
          settings.clearMode = action.clear_mode
          break
        case 'set_auto_mode':
          settings.autoMode = action.auto_mode
          break
        case 'set_follow_auto':
          settings.followAuto = action.follow_auto
          break
        default: {
          const unhandled: never = action
          void unhandled
        }
      }
      next = { ...state, settings }
      effects.push(...storeSetting(settings, action.action), { type: 'send', action })
      break
    }
    case 'disconnected':
      next = { ...state, showInFlight: false, discardId: null, discardCoolingDown: false }
      effects.push({ type: 'clear_discard_timer' })
      break
    case 'discard_settled':
      next = { ...state, discardId: null, discardCoolingDown: false }
      effects.push({ type: 'clear_discard_timer' })
      break
    case 'set_idle_image':
      next = { ...state, idleImage: event.image }
      break
    default: {
      const unhandled: never = event
      void unhandled
      break
    }
  }

  return { state: next, effects }
}
