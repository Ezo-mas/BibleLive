import type { ReactNode } from 'react'
import { EmptyState, BookIcon } from '../../components'
import { ReferenceCard } from './ReferenceCard'
import type { BibleReference } from '../../shared/types'

export interface PendingQueueProps {
  pending: BibleReference[]
  count: number
  selectedId: string | null
  onSelect: (id: string) => void
  /** Display settings and action buttons, rendered under the queue. */
  children?: ReactNode
}

/**
 * The STT pending queue: references detected from the sermon audio that are
 * waiting for the operator to approve. Order is arrival order and is never
 * re-sorted, because it mirrors the chronological detection stream.
 */
export function PendingQueue({
  pending,
  count,
  selectedId,
  onSelect,
  children,
}: PendingQueueProps) {
  return (
    <section className="operator-section operator-pending">
      <div className="operator-section-header">
        <h2 className="operator-section-title">Pending References</h2>
        <span className="operator-section-count">{count}</span>
      </div>

      <div className="operator-section-content">
        {pending.length === 0 ? (
          <EmptyState
            icon={<BookIcon />}
            title="No pending references"
            description="Bible references will appear here when detected by the audio pipeline"
          />
        ) : (
          <div className="ref-card-list">
            {pending.map(reference => (
              <ReferenceCard
                key={reference.id}
                reference={reference}
                selected={reference.id === selectedId}
                onSelect={() => onSelect(reference.id)}
              />
            ))}
          </div>
        )}
      </div>

      {children}
    </section>
  )
}
