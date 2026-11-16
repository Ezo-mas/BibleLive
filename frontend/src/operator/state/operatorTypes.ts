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

export type StagedReference =
  | { kind: 'pending'; id: string }
  | { kind: 'manual'; passage: Passage }
  | { kind: 'history'; reference: BibleReference }

export interface OperatorSettings {
  verseLimit: VerseLimit
  theme: DisplayTheme
  clearMode: ClearMode
  autoMode: AutoMode
  followAuto: boolean
}

export interface OperatorReducerState {
  pending: BibleReference[]
  history: BibleReference[]
  pendingCount: number
  historyCount: number
  currentDisplay: BibleReference | null
  seenPassage: boolean
  staged: StagedReference | null
  showInFlight: boolean
  discardId: string | null
  discardCoolingDown: boolean
  followAlongHint: FollowAlongHint | null
  displayConnected: boolean
  settings: OperatorSettings
  confirmed: OperatorSettings
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
  | { type: 'disconnected' }
  | { type: 'discard_settled' }
  | { type: 'set_idle_image'; image: IdleImage | null }

export type OperatorEvent = OperatorMessage | OperatorIntent

export interface OperatorTransition {
  state: OperatorReducerState
  effects: OperatorEffect[]
}
