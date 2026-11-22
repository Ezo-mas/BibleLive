import { useId, useRef } from 'react'
import type { MouseEvent } from 'react'
import { ArrowDownIcon, ArrowUpIcon, BookIcon, Button, XIcon } from '../../components'
import { useI18n } from '../../shared/i18nContext'
import { formatReference } from '../../shared/reference'
import type { BibleReference } from '../../shared/types'
import { PanelHint } from './PanelHint'
import { PanelMenu } from './PanelMenu'
import { RowRemoveButton, RowShowButton } from './RowButtons'
import { samePassage } from '../passageKey'
import { useCursorFocus } from '../hooks/useRowCursor'
import { TOUCH_LAYOUT, useOperatorMedia } from '../hooks/useOperatorMedia'
import type { PlanItem } from '../hooks/usePlan'

export interface PlanPanelProps {
  items: PlanItem[]
  /** Refreshed excerpts, by item id. */
  snippets?: Record<string, string>
  /** The passage in the display preview, to mark its matching Plan entry. */
  currentDisplay?: BibleReference | null
  displayConnected: boolean
  live: boolean
  /** The highlighted entry (the keyboard cursor), or null when it is not in the plan. */
  selectedId?: string | null
  /** The cursor's keyboard moves so far; a move onto an entry gives that entry focus. */
  keyboardMoves?: number
  /** Highlight an entry (click or focus); nothing is sent. */
  onSelect: (id: string) => void
  /** False while the socket is closed. */
  canShow?: boolean
  /** Show the entry: coordinates only, for the server to resolve. */
  onShow: (item: PlanItem) => void
  onRemove: (id: string) => void
  onMove: (id: string, direction: -1 | 1) => void
  onClear: () => void
}

/** A click on a row's own button is that button's; anywhere else on the row highlights it. */
function onRowClick(event: MouseEvent, select: () => void) {
  if (!(event.target as Element).closest('button')) select()
}

/**
 * The plan: passages the operator lined up before the service, in the order they
 * expect to need them. Always in view, under ON DISPLAY.
 *
 * It is the pending queue's counterpart — the queue holds what was heard, the
 * plan holds what is expected. A row shows its number, reference, excerpt, and
 * an "On screen" tag when it matches the passage on a connected, live display. A click
 * highlights the row. Double-click or Enter shows it using the existing display action. The plan
 * never touches the queue, and entries stay after they are shown, since a verse is
 * often read twice.
 */
export function PlanPanel({
  items,
  snippets = {},
  currentDisplay = null,
  displayConnected,
  live,
  selectedId = null,
  keyboardMoves = 0,
  onSelect,
  canShow = true,
  onShow,
  onRemove,
  onMove,
  onClear,
}: PlanPanelProps) {
  const { t } = useI18n()
  const touchLayout = useOperatorMedia(TOUCH_LAYOUT)
  const headingId = useId()
  const listRef = useRef<HTMLOListElement>(null)
  useCursorFocus(listRef, selectedId, keyboardMoves)
  return (
    <section className="operator-section operator-plan" aria-labelledby={headingId}>
      <div className="operator-card-head">
        <div className="operator-card-title">
          <h2 className="operator-section-title" id={headingId}>
            {t('plan.title')}
          </h2>
          <span className="operator-section-count">{items.length}</span>
        </div>
        <span className="operator-card-grow" />
        <PanelMenu
          label={t('plan.options')}
          placement="beside"
          items={[
            {
              label: t('plan.clear'),
              icon: <XIcon />,
              onSelect: onClear,
              disabled: items.length === 0,
              destructive: true,
            },
          ]}
        />
      </div>

      <div className="operator-section-content">
        {items.length === 0 ? (
          <PanelHint icon={<BookIcon />} title={t('plan.empty')} hint={t('plan.emptyHint')} />
        ) : (
          <ol className="plan-list" ref={listRef}>
            {items.map((item, index) => {
              const label = formatReference(item)
              const previewId = `${item.id}-preview`
              const onScreenId = `${item.id}-onscreen`
              const selected = item.id === selectedId
              const onScreen =
                live &&
                displayConnected &&
                currentDisplay !== null &&
                samePassage(item, currentDisplay)
              // Text looked up since (in the current translation), else the remembered start.
              const preview = snippets[item.id] || item.snippet || ''
              const describedBy = [onScreen ? onScreenId : null, preview ? previewId : null]
                .filter(Boolean)
                .join(' ')
              return (
                <li
                  key={item.id}
                  data-cursor-row={item.id}
                  className={`ref-item plan-item${selected ? ' is-selected' : ''}`}
                  onClick={event => onRowClick(event, () => onSelect(item.id))}
                  // As in Pending: a double-click shows the row without selecting a word.
                  onMouseDown={event => {
                    if (event.detail > 1) event.preventDefault()
                  }}
                  onDoubleClick={event => {
                    if (
                      !touchLayout &&
                      canShow &&
                      !(event.target as Element).closest('.plan-row-tools')
                    ) {
                      onSelect(item.id)
                      onShow(item)
                    }
                  }}
                >
                  <span className="plan-row-number" aria-hidden="true">
                    {index + 1}
                  </span>
                  <div
                    className="plan-row-details"
                    role="button"
                    tabIndex={0}
                    aria-pressed={selected}
                    aria-label={t('row.highlightAria', { ref: label })}
                    aria-describedby={describedBy || undefined}
                    onFocus={() => onSelect(item.id)}
                    onKeyDown={event => {
                      if (event.key === ' ') {
                        event.preventDefault()
                        onSelect(item.id)
                      }
                    }}
                  >
                    <div className="plan-row-main">
                      <span className="plan-row-title">{label}</span>
                      {onScreen && (
                        <span className="tag plan-row-onscreen" id={onScreenId}>
                          {t('plan.onScreen')}
                        </span>
                      )}
                    </div>
                    {preview && (
                      <p className="ref-card-text plan-row-text" id={previewId}>
                        {preview}
                      </p>
                    )}
                  </div>
                  <div className="plan-row-tools">
                    <RowRemoveButton
                      label={t('plan.remove', { ref: label })}
                      onClick={() => onRemove(item.id)}
                    />
                    <Button
                      variant="ghost"
                      size="sm"
                      iconOnly
                      icon={<ArrowUpIcon />}
                      aria-label={t('plan.moveUp', { ref: label })}
                      disabled={index === 0}
                      onClick={() => onMove(item.id, -1)}
                    />
                    <Button
                      variant="ghost"
                      size="sm"
                      iconOnly
                      icon={<ArrowDownIcon />}
                      aria-label={t('plan.moveDown', { ref: label })}
                      disabled={index === items.length - 1}
                      onClick={() => onMove(item.id, 1)}
                    />
                    <RowShowButton
                      referenceLabel={label}
                      selected={selected}
                      disabled={!canShow}
                      onClick={() => onShow(item)}
                    />
                  </div>
                </li>
              )
            })}
          </ol>
        )}
      </div>
    </section>
  )
}
