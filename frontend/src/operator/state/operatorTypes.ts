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
  Passage,
  VerseLimit,
} from '../../shared/types'

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
  // What Up Next holds. One piece of state, so a queued selection and a manual
  // passage can never both look staged.
  staged: StagedReference | null
  // True between pressing Show and the server's answer. Repeated clicks must not
  // queue the same passage twice, and a confirmation that never comes (the
  // socket dropped) must not leave Show disabled — see the status effect in useOperatorState.
  showInFlight: boolean
  discardId: string | null
  discardCoolingDown: boolean
  // The latest follow-along hint. Transient: never in a snapshot, and only ever
  // offered while its `display_id` is still the passage on screen.
  followAlongHint: FollowAlongHint | null
  displayConnected: boolean
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

/** Socket availability and double-click timing are checked by the owning hooks. */
export type OperatorIntent =
  | { type: 'stage'; staged: StagedReference | null }
  | { type: 'select_by'; step: number }
  | { type: 'show_next' }
  | { type: 'show_direct'; request: Omit<DisplayReferenceAction, 'action'> }
  | { type: 'approve_selected' }
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
