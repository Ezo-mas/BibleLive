import { EmptyState, ClockIcon } from '../../components'
import { useI18n } from '../../shared/i18nContext'
import { ReferenceCard } from './ReferenceCard'
import { formatReference } from '../../shared/reference'
import type { BibleReference } from '../../shared/types'

export interface HistoryPanelProps {
  /** Recent entries, newest first, one per passage. */
  history: BibleReference[]
  /** The history entry staged in Up Next, or null when none is (or it aged out). */
  selectedId: string | null
  onSelect: (reference: BibleReference) => void
  /** Double-click: show the entry again without staging it first. */
  onShow: (reference: BibleReference) => void
}

/**
 * Session history of references that reached the display, newest first.
 *
 * Read-only with respect to the server: the list and its order come from the
 * server's history (one row per passage — showing a passage again moves its row
 * to the top instead of adding another), and selecting an entry here only stages
 * it in Up Next. Showing it again is a separate, server-confirmed action — a
 * double-click is the same request without the staging step — so nothing in this
 * panel can change what the audience is looking at without that request.
 *
 * Entries are identified by their server-assigned ID, never by position, so a
 * selection survives the list shifting underneath it.
 *
 * Renders the body of the sidebar's History tab; rows are kept compact (one line
 * of verse text) because history is a place to look back, not to work in.
 */
export function HistoryPanel({ history, selectedId, onSelect, onShow }: HistoryPanelProps) {
  const { t } = useI18n()
  return (
    <div className="operator-section-content operator-history">
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
              onActivate={() => onShow(reference)}
              ariaLabel={t('history.stageAria', { ref: formatReference(reference) })}
            />
          ))}
        </div>
      )}
    </div>
  )
}
