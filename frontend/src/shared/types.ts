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
  created_at: string
}

/** Messages the server sends to display clients (ws /ws/display). */
export type DisplayMessage =
  | { type: 'reference'; data: BibleReference }
  | { type: 'clear'; data: Record<string, never> }

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
    }}
  | { type: 'pending_update'; data: { pending: BibleReference[]; count: number } }
  | { type: 'approval_confirmed'; data: { reference: BibleReference } }
  | { type: 'clear_confirmed'; data: Record<string, never> }
  | { type: 'pending_cleared'; data: Record<string, never> }
  | { type: 'connection_status'; data: { display: number; operator: number } }
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

export type OperatorAction = ApproveAction | ClearAction | ClearPendingAction

/** Snapshot of server state returned by GET /api/state. */
export interface AppStateSnapshot {
  current_display: BibleReference | null
  pending: BibleReference[]
  history: BibleReference[]
  pending_count: number
  history_count: number
  display_connections: number
  operator_connections: number
}
