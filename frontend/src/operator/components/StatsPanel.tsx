export interface StatsPanelProps {
  pendingCount: number
  shownCount: number
  historyCount: number
}

/**
 * Session counters at a glance: what is queued, what has been shown, history.
 *
 * One compact row rather than a titled block — the sidebar also carries the
 * display settings and the history list, and history is the part an operator
 * scrolls back through during a service.
 */
export function StatsPanel({ pendingCount, shownCount, historyCount }: StatsPanelProps) {
  return (
    <section className="operator-section operator-stats" aria-label="Session statistics">
      <div className="operator-stats-grid">
        <div className="operator-stat">
          <span className="operator-stat-label">Pending</span>
          <span className="operator-stat-value">{pendingCount}</span>
        </div>
        <div className="operator-stat">
          <span className="operator-stat-label">Shown</span>
          <span className="operator-stat-value">{shownCount}</span>
        </div>
        <div className="operator-stat">
          <span className="operator-stat-label">History</span>
          <span className="operator-stat-value">{historyCount}</span>
        </div>
      </div>
    </section>
  )
}
