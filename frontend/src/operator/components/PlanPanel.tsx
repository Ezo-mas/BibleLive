import { BookIcon, Button, XIcon } from '../../components'
import { useI18n } from '../../shared/i18nContext'
import { formatReference } from '../../shared/reference'
import { PanelHint } from './PanelHint'
import { passageToReference } from '../passageApi'
import type { PlanItem } from '../hooks/usePlan'

export interface PlanPanelProps {
  items: PlanItem[]
  /** Click: look the passage up and stage it in Up Next. */
  onStage: (item: PlanItem) => void
  /** Double-click: show it at once, without staging. */
  onShow: (item: PlanItem) => void
  onRemove: (id: string) => void
  onMove: (id: string, direction: -1 | 1) => void
  onClear: () => void
}

/** How a planned passage reads, in the book name it was added under. */
function itemLabel(item: PlanItem): string {
  return formatReference(
    passageToReference({ ...item, verses: [], text: item.snippet, book_label: item.book_label })
  )
}

/**
 * The plan: passages the operator lined up before the service, in the order they
 * expect to need them.
 *
 * It is the pending queue's counterpart — the queue holds what was heard, the
 * plan holds what is expected — and it reaches the display by the same two doors
 * a searched passage does: a click stages the passage in Up Next (looked up
 * fresh), a double-click sends it as coordinates for the server to resolve. The
 * plan never touches the queue, and items stay in it after they are shown, since
 * a verse is often read twice.
 *
 * Renders the body of the sidebar's Plan tab.
 */
export function PlanPanel({ items, onStage, onShow, onRemove, onMove, onClear }: PlanPanelProps) {
  const { t } = useI18n()
  return (
    <>
      <div className="operator-section-content">
        {items.length === 0 ? (
          <PanelHint icon={<BookIcon />} title={t('plan.empty')} hint={t('plan.emptyHint')} />
        ) : (
          <ol className="plan-list">
            {items.map((item, index) => {
              const label = itemLabel(item)
              return (
                <li key={item.id} className="plan-row">
                  <button
                    type="button"
                    className="plan-row-main"
                    aria-label={t('plan.stageAria', { ref: label })}
                    onClick={() => onStage(item)}
                    onDoubleClick={() => onShow(item)}
                  >
                    <span className="plan-row-number" aria-hidden="true">
                      {index + 1}
                    </span>
                    <span className="plan-row-body">
                      <span className="plan-row-title">{label}</span>
                      {item.snippet && <span className="plan-row-snippet">{item.snippet}</span>}
                    </span>
                  </button>
                  <div className="plan-row-actions">
                    <button
                      type="button"
                      className="plan-row-action"
                      aria-label={t('plan.moveUp', { ref: label })}
                      disabled={index === 0}
                      onClick={() => onMove(item.id, -1)}
                    >
                      ↑
                    </button>
                    <button
                      type="button"
                      className="plan-row-action"
                      aria-label={t('plan.moveDown', { ref: label })}
                      disabled={index === items.length - 1}
                      onClick={() => onMove(item.id, 1)}
                    >
                      ↓
                    </button>
                    <button
                      type="button"
                      className="plan-row-action"
                      aria-label={t('plan.remove', { ref: label })}
                      onClick={() => onRemove(item.id)}
                    >
                      <XIcon aria-hidden="true" />
                    </button>
                  </div>
                </li>
              )
            })}
          </ol>
        )}
      </div>

      <div className="operator-queue-footer">
        <Button variant="ghost" size="sm" onClick={onClear} disabled={items.length === 0}>
          {t('plan.clear')}
        </Button>
        <span className="operator-queue-hint">{t('plan.hint')}</span>
      </div>
    </>
  )
}
