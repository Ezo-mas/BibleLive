import { DEFAULT_STREAM_OVERLAY_OPTIONS } from '../../display/streamOverlayOptions'
import { coerceClearMode } from '../../shared/clearMode'
import { readIdleImage } from '../../shared/idleScreen'
import type { MessageKey } from '../../shared/i18n'
import type { BibleReference, DisplaySettings } from '../../shared/types'
import { coerceDisplayTheme, coerceVerseLimit } from '../../shared/verseText'
import { coerceAutoMode } from '../autoMode'
import { dedupeHistory } from '../passageKey'
import type {
  NoticeMessage,
  OperatorEffect,
  OperatorEvent,
  OperatorReducerState,
  OperatorSettings,
  OperatorTransition,
  OptimisticSettingAction,
  RowCursor,
} from './operatorTypes'

/**
 * How many history entries a state snapshot carries (the server sends the last
 * 20, see `SNAPSHOT_HISTORY` in `backend/core/server/display_session.py`). The console
 * keeps the same window, so an entry folded in from a confirmation cannot grow
 * the list past what a reconnect would show. `history_count` still reports the
 * server's total.
 */
const HISTORY_WINDOW = 20

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
  clear_history: 'notice.errClearHistory',
  discard_pending: 'notice.errDiscardPending',
  set_verse_limit: 'notice.errVerseLimit',
  set_display_theme: 'notice.errTheme',
  set_clear_mode: 'notice.errClearMode',
  set_stream_overlay: 'notice.errStreamOverlay',
  set_live: 'notice.errLive',
  set_auto_mode: 'notice.errAutoMode',
  set_follow_auto: 'notice.errFollowAuto',
}

/**
 * Actions the console applies optimistically. When the server rejects one, the
 * control has to go back to the last confirmed value or it would keep claiming
 * a setting the display is not using (each one's revert is in `operatorReducer`,
 * which must cover every name here: its type says so).
 */
// How each optimistic control goes back to its last confirmed value when the server
// rejects it.
// Keyed by OptimisticSettingAction['action'], so leaving one out does not type-check;
// it is only read after isOptimisticAction, so an inherited property can never match.
const SETTING_KEYS = {
  set_verse_limit: 'verseLimit',
  set_display_theme: 'theme',
  set_clear_mode: 'clearMode',
  set_stream_overlay: 'streamOverlay',
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
    streamOverlay: DEFAULT_STREAM_OVERLAY_OPTIONS,
    live: true,
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
    cursor: null,
    showInFlight: false,
    discardId: null,
    discardCoolingDown: false,
    followAlongHint: null,
    displayConnected: null,
    settings,
    confirmed: { ...settings },
    idleImage: null,
  }
}

/**
 * Keep the cursor across a queue update while its detection is still queued.
 *
 * Detections arrive while an operator is deciding, so an arrival must not move the
 * highlight; a detection that has left the queue (shown, removed, or approved by
 * another console) takes the highlight with it, so Enter can never act on a row that
 * is gone. A plan row is unaffected: the plan is not the queue.
 */
function keepCursor(cursor: RowCursor | null, pending: BibleReference[]) {
  return cursor?.list === 'pending' && !pending.some(reference => reference.id === cursor.id)
    ? null
    : cursor
}

function displaySettings(data: DisplaySettings) {
  return {
    verseLimit: coerceVerseLimit(data.verse_limit),
    theme: coerceDisplayTheme(data.theme),
    clearMode: coerceClearMode(data.clear_mode),
    streamOverlay: data.stream_overlay ?? DEFAULT_STREAM_OVERLAY_OPTIONS,
    // An older server omits it, and an older server is always live.
    live: data.live !== false,
  }
}

// Only verse limit and theme are also kept in localStorage, so
// each entry is written out.
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
  // Notices are for errors only: what the operator did shows on screen already.
  const notice = (tone: 'info' | 'error', message: NoticeMessage) =>
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
      // Absent when talking to an older server, which leaves the mode off.
      if (data.auto_mode) settings.autoMode = confirmed.autoMode = coerceAutoMode(data.auto_mode)
      // Absent when talking to an older server, which leaves follow-along auto-show off.
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
        // Hints are not part of a snapshot; one from before a reconnect is stale.
        followAlongHint: null,
        displayConnected: data.display_connections > 0,
        settings,
        confirmed,
        idleImage: data.display_settings ? readIdleImage(data.display_settings) : state.idleImage,
        // A reconnect can land while a row is highlighted; keep it if the queue
        // still holds it so a dropped socket does not undo the operator's work.
        cursor: keepCursor(state.cursor, data.pending),
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
        cursor: keepCursor(state.cursor, event.data.pending),
        discardCoolingDown: state.discardCoolingDown || startCooldown,
      }
      if (startCooldown) effects.push({ type: 'start_discard_cooldown' })
      break
    }
    // The server pushes the approved reference; fold it into history and the
    // counters so the sidebar is not stale until the next snapshot.
    case 'approval_confirmed':
    case 'display_confirmed': {
      // For display_confirmed, the server resolved the manual passage and pushed
      // it to the displays, so this is the first moment the console can say it is
      // on screen. The reference comes back server-built: manual, with no confidence.
      const shown = event.data.reference
      // A passage follow-along showed by itself answers no Show of this console:
      // the operator's own request stays in flight.
      const followed = event.type === 'display_confirmed' && shown.source === 'follow'
      next = {
        ...state,
        history: dedupeHistory([shown, ...state.history]).slice(0, HISTORY_WINDOW),
        historyCount: state.historyCount + 1,
        currentDisplay: shown,
        seenPassage: true,
        showInFlight: followed ? state.showInFlight : false,
      }
      if (event.type === 'approval_confirmed') effects.push({ type: 'flash', target: 'approve' })
      break
    }
    case 'follow_along':
      // Replaces any earlier hint; whether it still applies is decided against
      // the display at render time, so arrival order cannot matter.
      next = { ...state, followAlongHint: event.data }
      break
    case 'clear_confirmed':
      next = { ...state, currentDisplay: null, followAlongHint: null }
      effects.push({ type: 'flash', target: 'clear' })
      break
    case 'pending_cleared':
      effects.push({ type: 'flash', target: 'clearPending' })
      break
    case 'history_cleared':
      next = {
        ...state,
        history: dedupeHistory(event.data.history),
        historyCount: event.data.history_count,
      }
      break
    case 'connection_status':
      next = { ...state, displayConnected: event.data.display > 0 }
      break
    case 'display_settings_confirmed': {
      const settings = displaySettings(event.data)
      next = {
        ...state,
        settings: { ...state.settings, ...settings },
        confirmed: { ...state.confirmed, ...settings },
        idleImage: readIdleImage(event.data),
      }
      break
    }
    case 'auto_mode_confirmed': {
      const autoMode = coerceAutoMode(event.data.auto_mode)
      next = {
        ...state,
        settings: { ...state.settings, autoMode },
        confirmed: { ...state.confirmed, autoMode },
      }
      break
    }
    case 'follow_auto_confirmed': {
      const followAuto = event.data.follow_auto
      next = {
        ...state,
        settings: { ...state.settings, followAuto },
        confirmed: { ...state.confirmed, followAuto },
      }
      break
    }
    case 'bible_version_confirmed':
      effects.push({ type: 'bible_version', data: event.data })
      break
    case 'error': {
      const { action: echoed, message: reason } = event.data
      // The server echoes an unknown action as sent, so it can be any JSON value.
      const action = typeof echoed === 'string' ? echoed : ''
      const optimistic = isOptimisticAction(action)
      effects.push({ type: 'log_error', reason })
      if (action === 'discard_pending') {
        next = { ...next, discardId: null, discardCoolingDown: false }
        effects.push({ type: 'clear_discard_timer' })
      }
      // A rejected or failed action is over: let the operator try again.
      if (action === 'approve' || action === 'display_reference')
        next = { ...next, showInFlight: false }
      notice('error', {
        key: optimistic ? 'notice.reverted' : 'notice.failed',
        params: {
          prefix: {
            // Own keys only: an echoed "constructor" must not find Object.prototype's.
            key: Object.prototype.hasOwnProperty.call(ERROR_PREFIX_KEYS, action)
              ? ERROR_PREFIX_KEYS[action]
              : 'notice.errGeneric',
          },
          reason:
            reason === 'Reference not found or already processed'
              ? { key: 'notice.reasonReferenceUnavailable' }
              : reason,
        },
      })
      // An optimistic change is only provisional. Put the last confirmed value
      // back so the console matches what the display is really using.
      if (optimistic) {
        const key = SETTING_KEYS[action]
        next = { ...next, settings: { ...state.settings, [key]: state.confirmed[key] } }
        effects.push(...storeSetting(next.settings, action))
      }
      break
    }
    case 'set_cursor':
      next = { ...state, cursor: event.cursor }
      break
    // Up and down a list: the cursor moves by `step`, held at either end; from
    // another list (or none) it lands on the first row.
    case 'move_cursor': {
      const ids =
        event.list === 'pending' ? state.pending.map(reference => reference.id) : event.planIds
      if (ids.length === 0) break
      const index = state.cursor?.list === event.list ? ids.indexOf(state.cursor.id) : -1
      const target = index === -1 ? 0 : Math.min(Math.max(index + event.step, 0), ids.length - 1)
      next = { ...state, cursor: { list: event.list, id: ids[target] } }
      break
    }
    // Every display request is one of two doors, and they stay separate on the
    // wire: a queued detection is approved by ID (approve_pending), while a passage
    // the operator chose — by hand, from the plan or from history — is sent as
    // coordinates for the server to resolve itself.
    case 'show_direct':
      if (!state.showInFlight) {
        next = { ...state, showInFlight: true }
        effects.push({ type: 'send', action: { action: 'display_reference', ...event.request } })
      }
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
    case 'clear_history':
      effects.push({ type: 'send', action: { action: 'clear_history' } })
      break
    case 'set_live':
      effects.push({ type: 'send', action: { action: 'set_live', live: event.live } })
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
        case 'set_stream_overlay':
          settings.streamOverlay = action.stream_overlay
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
    // Until the next snapshot, whether a display is connected is not known.
    case 'disconnected':
      next = {
        ...state,
        showInFlight: false,
        discardId: null,
        discardCoolingDown: false,
        displayConnected: null,
      }
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
