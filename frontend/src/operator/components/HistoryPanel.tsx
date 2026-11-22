import { EmptyState, ClockIcon, XIcon } from '../../components'
import { useI18n } from '../../shared/i18nContext'
import { PanelMenu } from './PanelMenu'
import { ReferenceCard } from './ReferenceCard'
import type { BibleReference } from '../../shared/types'

export interface HistoryPanelProps {
  /** Recent entries, newest first, one per passage. */
  history: BibleReference[]
  selectedId: string | null
  onSelect: (id: string) => void
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
 * Showing a passage again moves its row to the top. Double-click or Enter on a
 * focused row replays it with the same coordinates-only request Find Scripture sends.
 */
export function HistoryPanel({
  history,
  selectedId,
  onSelect,
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
                selected={reference.id === selectedId}
                onTouchSelect={() => onSelect(reference.id)}
                showConfidence={false}
                onShow={() => onShow(reference)}
                showDisabled={!canShow}
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
        {history.length > 0 && canShow && (
          <span className="operator-queue-hint">{t('history.replayHint')}</span>
        )}
      </div>
    </>
  )
}
