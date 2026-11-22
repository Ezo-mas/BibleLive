import { useRef } from 'react'
import { BookIcon } from '../../components'
import { useI18n } from '../../shared/i18nContext'
import { formatReference } from '../../shared/reference'
import { useCursorFocus } from '../hooks/useRowCursor'
import { TOUCH_LAYOUT, useOperatorMedia } from '../hooks/useOperatorMedia'
import { PanelHint } from './PanelHint'
import { ReferenceCard } from './ReferenceCard'
import { RowRemoveButton } from './RowButtons'
import type { BibleReference } from '../../shared/types'

export interface PendingQueueProps {
  pending: BibleReference[]
  /** The highlighted detection (the keyboard cursor), or null when it is not in the queue. */
  selectedId: string | null
  /** The cursor's keyboard moves so far; a move onto a row gives that row focus. */
  keyboardMoves?: number
  /** Highlight a row (click or focus); nothing is sent. */
  onSelect: (id: string) => void
  /** Approve this detection by ID. */
  onShow: (id: string) => void
  onRemove: (id: string) => void
  discardInFlight?: boolean
  /** False while the socket is closed: Show then has nowhere to go. */
  canShow?: boolean
}

/**
 * The STT pending queue: references detected from the sermon audio that are
 * waiting for the operator to approve. Order is arrival order and is never
 * re-sorted, because it mirrors the chronological detection stream.
 *
 * A click highlights a row. Double-click or Enter on the highlight approves it.
 * A new detection never takes the highlight.
 *
 * Renders the body of the sidebar's Pending tab; the tabs (and their counts) are
 * in `SidePanel`.
 */
export function PendingQueue({
  pending,
  selectedId,
  keyboardMoves = 0,
  onSelect,
  onShow,
  onRemove,
  discardInFlight = false,
  canShow = true,
}: PendingQueueProps) {
  const { t } = useI18n()
  const touchLayout = useOperatorMedia(TOUCH_LAYOUT)
  const listRef = useRef<HTMLDivElement>(null)
  useCursorFocus(listRef, selectedId, keyboardMoves)
  return (
    <div className="operator-section-content">
      {pending.length === 0 ? (
        <PanelHint icon={<BookIcon />} title={t('queue.empty')} />
      ) : (
        <div className="ref-card-list" ref={listRef}>
          {pending.map(reference => {
            const selected = reference.id === selectedId
            return (
              <ReferenceCard
                key={reference.id}
                reference={reference}
                selected={selected}
                touchLayout={touchLayout}
                onSelect={() => onSelect(reference.id)}
                onShow={() => onShow(reference.id)}
                showDisabled={!canShow}
                removeAction={
                  <RowRemoveButton
                    label={t('queue.remove', { ref: formatReference(reference) })}
                    disabled={!canShow || discardInFlight}
                    onClick={() => onRemove(reference.id)}
                  />
                }
              />
            )
          })}
        </div>
      )}
    </div>
  )
}
