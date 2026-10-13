/** A single verse with its number, as returned by the backend for display. */
export interface VerseText {
  verse: number
  text: string
}

/**
 * A Bible reference.
 *
 * `confidence` is null for passages the operator picked by hand: those are not
 * detections, so they carry no confidence rather than an invented one.
 * `source` says which kind it is (absent when talking to an older server).
 */
export interface BibleReference {
  id: string
  book: string
  chapter: number
  verse_start: number | null
  verse_end: number | null
  confidence: 'high' | 'medium' | 'low' | null
  inferred: boolean
  text: string
  /** Individual verses; empty when the server has not resolved text yet. */
  verses: VerseText[]
  source?: 'detected' | 'manual'
  created_at: string
}

/** One book of the canonical canon, as served by GET /api/books. */
export interface ScriptureBook {
  name: string
  /** Number of chapters, so the console never guesses a chapter count. */
  chapters: number
  /** 1-based canonical position (Genesis=1). */
  order: number
  /**
   * Short form from the canon, e.g. 'gn'. Optional because a server that
   * predates it serves only name/chapters/order; matching falls back to the
   * name alone in that case.
   */
  abbrev?: string
  /**
   * Alternative name forms the parser accepts, e.g. ['psalms', 'psalm'].
   * Also optional for the same reason as `abbrev`.
   */
  variants?: string[]
}

/** GET /api/books response. */
export interface BooksResponse {
  books: ScriptureBook[]
}

/** A passage resolved by the server, ready to stage and then display. */
export interface Passage {
  book: string
  chapter: number
  verse_start: number
  verse_end: number
  verses: VerseText[]
  text: string
}

/** How many verses the display renders at once; 'all' shows the full passage. */
export type VerseLimit = number | 'all'

/** Background theme for the audience display. Projectors default to 'dark'. */
export type DisplayTheme = 'dark' | 'light'

/** Presentation settings for the audience display, chosen by the operator. */
export interface DisplaySettings {
  verse_limit: VerseLimit
  theme: DisplayTheme
}

/**
 * Which newly detected references the server displays without operator action.
 * Confidence order is low < medium < high, so 'medium_and_up' ('Med+') covers
 * high as well; 'off' sends every detection to the pending queue.
 */
export type AutoMode = 'off' | 'high' | 'medium_and_up' | 'all'

/** Messages the server sends to display clients (ws /ws/display). */
export type DisplayMessage =
  | { type: 'reference'; data: BibleReference }
  | { type: 'clear'; data: Record<string, never> }
  | { type: 'display_settings'; data: DisplaySettings }

/** Messages the server sends to operator clients (ws /ws/operator). */
export type OperatorMessage =
  | {
      type: 'state_snapshot'
      data: {
        current_display: BibleReference | null
        pending: BibleReference[]
        history: BibleReference[]
        pending_count: number
        history_count: number
        display_connections: number
        operator_connections: number
        display_settings?: DisplaySettings
        /** Absent when talking to an older server. */
        auto_mode?: AutoMode
      }
    }
  | { type: 'pending_update'; data: { pending: BibleReference[]; count: number } }
  | { type: 'approval_confirmed'; data: { reference: BibleReference } }
  | { type: 'clear_confirmed'; data: Record<string, never> }
  | { type: 'pending_cleared'; data: Record<string, never> }
  | { type: 'connection_status'; data: { display: number; operator: number } }
  | { type: 'display_settings_confirmed'; data: DisplaySettings }
  | { type: 'auto_mode_confirmed'; data: { auto_mode: AutoMode } }
  /** Confirms a manual passage reached the display (action `display_reference`). */
  | { type: 'display_confirmed'; data: { reference: BibleReference } }
  | { type: 'error'; data: { message: string; action: string } }

/** Messages the operator client sends to the server. */
export interface ApproveAction {
  action: 'approve'
  reference_id: string
}

export interface ClearAction {
  action: 'clear'
}

export interface ClearPendingAction {
  action: 'clear_pending'
}

export interface SetVerseLimitAction {
  action: 'set_verse_limit'
  verse_limit: VerseLimit
}

export interface SetDisplayThemeAction {
  action: 'set_display_theme'
  theme: DisplayTheme
}

export interface SetAutoModeAction {
  action: 'set_auto_mode'
  auto_mode: AutoMode
}

/**
 * Ask the server to put an operator-selected passage on the display.
 *
 * Coordinates only: there is no reference ID (nothing is queued), no confidence,
 * and no text — the server re-resolves the passage against its own canon and
 * Bible database, so a manual selection can never be passed off as a detection.
 */
export interface DisplayReferenceAction {
  action: 'display_reference'
  book: string
  chapter: number
  verse_start: number | null
  verse_end: number | null
}

/**
 * The operator token, sent as the first message on every operator socket.
 *
 * Not in the URL: URLs are written to server and proxy logs. A server with auth
 * on accepts nothing else first; one without auth ignores it.
 */
export interface AuthenticateAction {
  action: 'authenticate'
  token: string
}

export type OperatorAction =
  | ApproveAction
  | ClearAction
  | ClearPendingAction
  | SetVerseLimitAction
  | SetDisplayThemeAction
  | SetAutoModeAction
  | DisplayReferenceAction

/** Snapshot of server state returned by GET /api/state. */
export interface AppStateSnapshot {
  current_display: BibleReference | null
  pending: BibleReference[]
  history: BibleReference[]
  pending_count: number
  history_count: number
  display_connections: number
  operator_connections: number
  display_settings?: DisplaySettings
  auto_mode?: AutoMode
}

/**
 * Audio pipeline health, from GET /api/pipeline/status.
 *
 * Polled over HTTP rather than pushed on the operator socket, so the WebSocket
 * protocol is unchanged. 'unknown' means the server has had no report since it
 * started (a console used without the pipeline stays quiet); 'offline' means
 * reports stopped arriving, which is how a dead pipeline shows up.
 */
export type PipelineHealth = 'unknown' | 'ok' | 'degraded' | 'offline'

export interface PipelineStatus {
  status: PipelineHealth
  language: string | null
  /** One sentence per thing that has stopped working; empty unless degraded. */
  problems: string[]
  seconds_since_report: number | null
}
