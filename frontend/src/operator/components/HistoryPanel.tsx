import { EmptyState, ClockIcon, XIcon } from '../../components'
import { useI18n } from '../../shared/i18nContext'
import { PanelMenu } from './PanelMenu'
import { ReferenceCard } from './ReferenceCard'
import { RowShowButton } from './RowButtons'
import type { BibleReference } from '../../shared/types'

export interface HistoryPanelProps {
  /** Recent entries, newest first, one per passage. */
  history: BibleReference[]
  /** Show the entry again: coordinates only, for the server to resolve. */
  onShow: (reference: BibleReference) => void
  onClearHistory: () => void
  /** False while the socket is closed. */
  canShow?: boolean
}

/**
 * Session history of references that reached the display, newest first.
 *
 * The list and its order come from the server's history (one row per passage).
 * Showing a passage again moves its row to the top. Each row's Show puts the passage on
 * screen again with the same coordinates-only request Find Scripture sends. Rows
 * take no keyboard cursor. The panel menu clears the entire history.
 */
export function HistoryPanel({
  history,
  onShow,
  onClearHistory,
  canShow = true,
}: HistoryPanelProps) {
  const { t } = useI18n()
  return (
    <>
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
      <div className="operator-queue-footer">
        <PanelMenu
          label={t('history.options')}
          placement="above"
          items={[
            {
              label: t('history.clear'),
              icon: <XIcon />,
              disabled: history.length === 0 || !canShow,
              destructive: true,
              onSelect: onClearHistory,
            },
          ]}
        />
      </div>
    </>
  )
}
