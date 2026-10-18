import { EmptyState, ClockIcon } from '../../components'
import { useI18n } from '../../shared/i18nContext'
import { ReferenceCard } from './ReferenceCard'
import { formatReference } from '../../shared/reference'
import type { BibleReference } from '../../shared/types'

export interface HistoryPanelProps {
  /** Recent entries, newest first, exactly as the server sent them. */
  history: BibleReference[]
  /** Total entries the server has recorded (the list above is its window). */
  count: number
  /** The history entry staged in Up Next, or null when none is (or it aged out). */
  selectedId: string | null
  onSelect: (reference: BibleReference) => void
}

/**
 * Session history of references that reached the display, newest first.
 *
 * Read-only with respect to the server: the list, its order, and its length all
 * come from the server's history, and selecting an entry here only stages it in
 * Up Next. Showing it again is a separate, server-confirmed action, so nothing
 * in this panel can change what the audience is looking at.
 *
 * Entries are identified by their server-assigned ID, never by position, so a
 * selection survives the list shifting underneath it (the server keeps the newest
 * twenty in a snapshot; older entries age out).
 *
 * Each card carries the same information the queue's cards do: the reference, its
 * verse text, and the reading taken from the entry's own `created_at` (when the
 * detection arrived, or when a manual passage was displayed), plus a badge that
 * names its source when it was not a detection.
 */
export function HistoryPanel({ history, count, selectedId, onSelect }: HistoryPanelProps) {
  const { t } = useI18n()
  return (
    <section className="operator-section operator-history" aria-label={t('history.aria')}>
      <div className="operator-section-header">
        <h2 className="operator-section-title">{t('history.title')}</h2>
        <span className="operator-section-count">{count}</span>
      </div>

      <div className="operator-section-content">
        {history.length === 0 ? (
          <EmptyState
            icon={<ClockIcon />}
            title={t('history.empty')}
            description={t('history.emptyHint')}
          />
        ) : (
          <div className="ref-card-list">
            {history.map(reference => (
              <ReferenceCard
                key={reference.id}
                reference={reference}
                selected={reference.id === selectedId}
                onSelect={() => onSelect(reference)}
                ariaLabel={t('history.stageAria', { ref: formatReference(reference) })}
              />
            ))}
          </div>
        )}
      </div>
    </section>
  )
}
