/** A single verse with its number, as returned by the backend for display. */
export interface VerseText {
  verse: number
  text: string
}

/** A Bible reference as produced by the parser and validated by the backend. */
export interface BibleReference {
  id: string
  book: string
  chapter: number
  verse_start: number | null
  verse_end: number | null
  confidence: 'high' | 'medium' | 'low'
  inferred: boolean
  text: string
  /** Individual verses; empty when the server has not resolved text yet. */
  verses: VerseText[]
  created_at: string
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

/** Messages the server sends to display clients (ws /ws/display). */
export type DisplayMessage =
  | { type: 'reference'; data: BibleReference }
  | { type: 'clear'; data: Record<string, never> }
  | { type: 'display_settings'; data: DisplaySettings }

/** Messages the server sends to operator clients (ws /ws/operator). */
export type OperatorMessage =
  | { type: 'state_snapshot'; data: { 
      current_display: BibleReference | null
      pending: BibleReference[]
      history: BibleReference[]
      pending_count: number
      history_count: number
      display_connections: number
      operator_connections: number
      display_settings?: DisplaySettings
    }}
  | { type: 'pending_update'; data: { pending: BibleReference[]; count: number } }
  | { type: 'approval_confirmed'; data: { reference: BibleReference } }
  | { type: 'clear_confirmed'; data: Record<string, never> }
  | { type: 'pending_cleared'; data: Record<string, never> }
  | { type: 'connection_status'; data: { display: number; operator: number } }
  | { type: 'display_settings_confirmed'; data: DisplaySettings }
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

export type OperatorAction =
  | ApproveAction
  | ClearAction
  | ClearPendingAction
  | SetVerseLimitAction
  | SetDisplayThemeAction

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
}
