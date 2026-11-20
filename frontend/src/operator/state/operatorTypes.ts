import type { MessageKey } from '../../shared/i18n'
import type { IdleImage } from '../../shared/idleScreen'
import type {
  AutoMode,
  BibleReference,
  ClearMode,
  DisplayReferenceAction,
  DisplayTheme,
  FollowAlongHint,
  OperatorAction,
  OperatorMessage,
  VerseLimit,
} from '../../shared/types'

/** The two lists the keyboard cursor can sit in. History has no cursor: its rows only show. */
export type CursorList = 'pending' | 'plan'

/**
 * The one keyboard cursor: the highlighted row, in Pending or in Plan, whichever the
 * operator last clicked or focused. Highlighting changes nothing on screen; Enter or the
 * row's Show displays it through the row's own door (a detection is approved by ID, a
 * plan entry is sent as coordinates). Held by ID, never by position, so a list moving
 * underneath it cannot move it to another row, and only the operator ever moves it: a
 * new detection never takes it.
 */
export interface RowCursor {
  list: CursorList
  id: string
}

export interface OperatorSettings {
  // The display is presentation-only, so this control lives here and the server
  // pushes the value to every projector. localStorage keeps the last choice
  // while the socket reconnects.
  verseLimit: VerseLimit
  // Display background. Projectors run dark, so that is the default here too.
  theme: DisplayTheme
  clearMode: ClearMode
  // Whether the audience display is on air. Never set optimistically: the console
  // shows only what the server confirmed, so it can never claim a dark screen that
  // the audience can still see.
  live: boolean
  // Auto-approve mode. Server-side state (persisted there too), so the console
  // only mirrors it: the snapshot on connect is the source of truth.
  autoMode: AutoMode
  // Follow-along auto-show, mirrored the same way: the server's value wins.
  followAuto: boolean
}

export interface OperatorReducerState {
  pending: BibleReference[]
  history: BibleReference[]
  pendingCount: number
  historyCount: number
  currentDisplay: BibleReference | null
  // Page-local: after a console reload on a cleared display, the preview shows the
  // picture until the next passage, because the protocol does not track this history.
  seenPassage: boolean
  // The highlighted row (see RowCursor). Dropped when its detection leaves the queue.
  cursor: RowCursor | null
  // True between pressing Show and the server's answer. Repeated clicks must not
  // queue the same passage twice, and a confirmation that never comes (the
  // socket dropped) must not leave Show disabled — see the status effect in useOperatorState.
  showInFlight: boolean
  discardId: string | null
  discardCoolingDown: boolean
  // The latest follow-along hint. Transient: never in a snapshot, and only ever
  // offered while its `display_id` is still the passage on screen.
  followAlongHint: FollowAlongHint | null
  // Whether any display page is connected; null until the server has said (a snapshot),
  // and again while the socket is down, so the console never claims a missing display
  // it has not been told about.
  displayConnected: boolean | null
  settings: OperatorSettings
  // The last values the server confirmed. Optimistic control changes fall back
  // to these when the server rejects them.
  confirmed: OperatorSettings
  // The display's idle picture, from the snapshot, settings echoes and this console's
  // own idle-screen requests. The server tells only displays when another console
  // changes it, so that change shows here after the next snapshot or echo.
  idleImage: IdleImage | null
}

export type OptimisticSettingAction = Extract<
  OperatorAction,
  {
    action:
      | 'set_verse_limit'
      | 'set_display_theme'
      | 'set_clear_mode'
      | 'set_auto_mode'
      | 'set_follow_auto'
  }
>

/** Translation is performed by the hook, using its current interface language. */
export interface NoticeMessage {
  key: MessageKey
  params?: Record<string, string | number | NoticeMessage | { join: NoticeMessage[] }>
}

export type OperatorEffect =
  | { type: 'notice'; tone: 'success' | 'info' | 'error'; message: NoticeMessage }
  | { type: 'flash'; target: 'approve' | 'clear' | 'clearPending' }
  | { type: 'store_verse_limit'; value: VerseLimit }
  | { type: 'store_display_theme'; value: DisplayTheme }
  | { type: 'send'; action: OperatorAction }
  | {
      type: 'bible_version'
      data: Extract<OperatorMessage, { type: 'bible_version_confirmed' }>['data']
    }
  | { type: 'log_error'; reason: string }
  | { type: 'start_discard_cooldown' }
  | { type: 'clear_discard_timer' }

/** Socket availability is checked by the owning hooks. */
export type OperatorIntent =
  | { type: 'set_cursor'; cursor: RowCursor | null }
  /**
   * Move the cursor `step` rows within `list`, held at either end; from outside the
   * list it lands on the first row. The plan lives in the browser, not here, so its
   * row IDs come with the intent; the queue's are the reducer's own.
   */
  | { type: 'move_cursor'; list: CursorList; step: number; planIds: readonly string[] }
  | { type: 'show_direct'; request: Omit<DisplayReferenceAction, 'action'> }
  | { type: 'approve_pending'; id: string }
  | { type: 'discard_pending'; id: string }
  | { type: 'clear_display' }
  | { type: 'clear_pending' }
  | { type: 'set_setting'; action: OptimisticSettingAction }
  | { type: 'set_live'; live: boolean }
  | { type: 'disconnected' }
  | { type: 'discard_settled' }
  | { type: 'set_idle_image'; image: IdleImage | null }

export type OperatorEvent = OperatorMessage | OperatorIntent

export interface OperatorTransition {
  state: OperatorReducerState
  effects: OperatorEffect[]
}
