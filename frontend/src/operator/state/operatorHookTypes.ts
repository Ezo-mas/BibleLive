import type { FormEvent } from 'react'
import type { WsStatus } from '../../shared/useWebSocket'
import type { DisplayTheme, VerseLimit } from '../../shared/verseText'
import type { IdleImage } from '../../shared/idleScreen'
import type {
  AutoMode,
  BibleReference,
  ClearMode,
  FollowAlongHint,
  PipelineStatus,
} from '../../shared/types'
import type { PassageQuery } from '../passageApi'
import type { PlanItem, PlanState } from '../hooks/usePlan'
import type { ScriptureSearchState } from '../hooks/useScriptureSearch'
import type { BibleVersionState } from '../hooks/useBibleVersion'
import type { IdleScreenState } from '../hooks/useIdleScreen'
import type { InputLevelsStore } from './inputLevelsStore'
import type { SpokenLanguageState } from '../hooks/useSpokenLanguage'
import type { FlashTarget, OperatorNotice } from '../hooks/useNotices'
import type { RowCursorState } from '../hooks/useRowCursor'

/** The socket as the header reads it: a chip only while it is not connected. */
export type ConnectionState = 'connected' | 'connecting' | 'disconnected'

export interface OperatorState {
  /** Raw WebSocket status, for callers that need the exact socket state. */
  status: WsStatus
  /** WebSocket status in the header's vocabulary. */
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
  seenPassage: boolean
  hasDisplayContent: boolean
  displayConnected: boolean
  /**
   * True only when the server has said no display page is connected (and the socket
   * is up): the On display warning chip. Unknown (before the snapshot, while
   * reconnecting) is not missing.
   */
  displayMissing: boolean
  /** Audio pipeline health; null while disconnected or before the first answer. */
  pipeline: PipelineStatus | null
  inputLevels: InputLevelsStore
  /**
   * Send a manual passage to the display: coordinates only, for the server to
   * resolve (`display_reference`). Used by Find Scripture, Plan, History and
   * follow-along; does nothing while disconnected.
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
  expandCurrentDisplay: () => void
  canExpandCurrentDisplay: boolean

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

  /** Show a queued detection: approved by ID (the row's Show, or Enter on its highlight). */
  approvePending: (id: string) => void
  /** Show a history entry again: coordinates only. */
  showHistory: (reference: BibleReference) => void
  discardPending: (id: string) => void
  discardInFlight: boolean

  /** Passages lined up in advance, kept in this browser. */
  plan: PlanState
  /** Refreshed Plan excerpts, by item id. */
  planSnippets: Record<string, string>
  /** The text the follow-along hint points at, once looked up; null until then. */
  followAlongText: string | null
  /** Show a planned passage: coordinates only. */
  showPlanItem: (item: PlanItem) => void
  /** The one keyboard cursor (highlighted row) in Pending or Plan. */
  cursor: RowCursorState

  clearDisplay: () => void
  clearPendingQueue: () => void
  clearHistory: () => void
  canClearDisplay: boolean
  canClearPending: boolean
  flash: FlashTarget | null

  verseLimit: VerseLimit
  displayTheme: DisplayTheme
  clearMode: ClearMode
  /** The picture the display shows while idle, so the preview can show it too; null for blank. */
  idleImage: IdleImage | null
  setDisplayVerseLimit: (next: VerseLimit) => void
  setDisplayBackground: (next: DisplayTheme) => void
  setDisplayClearMode: (next: ClearMode) => void

  /** Whether the audience display is on air, as the server last confirmed it. */
  live: boolean
  /** Ask the server to put the display on air or off; the switch moves on its confirmation. */
  setLive: (next: boolean) => void

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
