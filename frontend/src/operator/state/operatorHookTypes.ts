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
import type { SpokenLanguageState } from '../hooks/useSpokenLanguage'
import type { ConnectionState } from '../../components'
import type { FlashTarget, OperatorNotice } from '../hooks/useNotices'
import type { NextSource } from '../hooks/useStaging'

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
  seenPassage: boolean
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
  discardPending: (id: string) => void
  discardInFlight: boolean

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
  clearMode: ClearMode
  /** The picture the display shows while idle, so the preview can show it too; null for blank. */
  idleImage: IdleImage | null
  setDisplayVerseLimit: (next: VerseLimit) => void
  setDisplayBackground: (next: DisplayTheme) => void
  setDisplayClearMode: (next: ClearMode) => void

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
