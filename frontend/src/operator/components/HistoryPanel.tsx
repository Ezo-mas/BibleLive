import { EmptyState, ClockIcon } from '../../components'
import { ReferenceCard } from './ReferenceCard'
import type { BibleReference } from '../../shared/types'

export interface HistoryPanelProps {
  history: BibleReference[]
  count: number
}

/**
 * Session history of references that reached the display, newest first.
 * Bounded in memory by the server; nothing here is persisted.
 */
export function HistoryPanel({ history, count }: HistoryPanelProps) {
  return (
    <section className="operator-section operator-history">
      <div className="operator-section-header">
        <h2 className="operator-section-title">Recent History</h2>
        <span className="operator-section-count">{count}</span>
      </div>

      <div className="operator-section-content">
        {history.length === 0 ? (
          <EmptyState
            icon={<ClockIcon />}
            title="No history yet"
            description="Approved references will appear here"
          />
        ) : (
          <div className="ref-card-list">
            {history.map(reference => (
              <ReferenceCard key={reference.id} reference={reference} />
            ))}
          </div>
        )}
      </div>
    </section>
  )
}
