export interface StatsPanelProps {
  pendingCount: number
  shownCount: number
  historyCount: number
}

/** Session counters at a glance: what is queued, what has been shown, history. */
export function StatsPanel({ pendingCount, shownCount, historyCount }: StatsPanelProps) {
  return (
    <section className="operator-section operator-stats">
      <h2 className="operator-section-title">Statistics</h2>
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
