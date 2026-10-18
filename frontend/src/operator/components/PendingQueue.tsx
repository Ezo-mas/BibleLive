import type { ReactNode } from 'react'
import { BookIcon } from '../../components'
import { useI18n } from '../../shared/i18nContext'
import { PanelHint } from './PanelHint'
import { ReferenceCard } from './ReferenceCard'
import type { BibleReference } from '../../shared/types'

export interface PendingQueueProps {
  pending: BibleReference[]
  count: number
  selectedId: string | null
  onSelect: (id: string) => void
  /** The queue's footer (Clear Queue), pinned under the list. */
  children?: ReactNode
}

/**
 * The STT pending queue: references detected from the sermon audio that are
 * waiting for the operator to approve. Order is arrival order and is never
 * re-sorted, because it mirrors the chronological detection stream.
 *
 * When nothing is waiting the queue says so in the same compact form as the LIVE
 * and NEXT panels. An empty queue is the normal state between detections, so it
 * should not reserve a block of screen that the references will need.
 */
export function PendingQueue({
  pending,
  count,
  selectedId,
  onSelect,
  children,
}: PendingQueueProps) {
  const { t } = useI18n()
  return (
    <section className="operator-section operator-pending">
      <div className="operator-section-header">
        <h2 className="operator-section-title">{t('queue.title')}</h2>
        <span className="operator-section-count">{count}</span>
      </div>

      <div className="operator-section-content">
        {pending.length === 0 ? (
          <PanelHint icon={<BookIcon />} title={t('queue.empty')} hint={t('queue.emptyHint')} />
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
