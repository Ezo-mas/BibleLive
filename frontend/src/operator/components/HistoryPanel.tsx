import { EmptyState, ClockIcon } from '../../components'
import { useI18n } from '../../shared/i18nContext'
import { ReferenceCard } from './ReferenceCard'
import { RowShowButton } from './RowButtons'
import type { BibleReference } from '../../shared/types'

export interface HistoryPanelProps {
  /** Recent entries, newest first, one per passage. */
  history: BibleReference[]
  /** Show the entry again: coordinates only, for the server to resolve. */
  onShow: (reference: BibleReference) => void
  /** False while the socket is closed: Show then has nowhere to go. */
  canShow?: boolean
}

/**
 * Session history of references that reached the display, newest first.
 *
 * Read-only with respect to the server: the list and its order come from the
 * server's history (one row per passage — showing a passage again moves its row
 * to the top instead of adding another). Each row's Show puts the passage on
 * screen again with the same coordinates-only request Find Scripture sends. Rows
 * take no keyboard cursor and have no Remove: history is a place to look back.
 */
export function HistoryPanel({ history, onShow, canShow = true }: HistoryPanelProps) {
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
              showConfidence={false}
              actions={titleId => (
                <RowShowButton
                  titleId={titleId}
                  disabled={!canShow}
                  onClick={() => onShow(reference)}
                />
              )}
            />
          ))}
        </div>
      )}
    </div>
  )
}
