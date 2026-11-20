import { useId } from 'react'
import type { ReactNode } from 'react'
import { formatReference } from '../../shared/reference'
import { StatusBadge } from '../../components'
import { useI18n } from '../../shared/i18nContext'
import type { MessageKey } from '../../shared/i18n'
import type { BibleReference } from '../../shared/types'

export interface ReferenceCardProps {
  reference: BibleReference
  /** True when this row holds the keyboard cursor (the highlighted row). */
  selected?: boolean
  /** Highlight this row (a click or focus on it); nothing is sent. Omitted, the row is not selectable. */
  onSelect?: () => void
  /**
   * Whether the row carries the detection's confidence (a Low row's dashed edge, and
   * the word for screen readers and the tooltip). History leaves it out: confidence
   * belongs to a detection, not to something that was shown.
   */
  showConfidence?: boolean
  /** Remove sits beside the timestamp and Show uses the title as its description. */
  removeAction?: ReactNode
  actions?: (titleId: string) => ReactNode
}

/** Confidence in plain words, as message keys. */
const CONFIDENCE_LABELS: Record<'high' | 'medium' | 'low', MessageKey> = {
  high: 'ref.confidence.high',
  medium: 'ref.confidence.medium',
  low: 'ref.confidence.low',
}

/**
 * Arrival time as a local clock reading, or null when the timestamp is unusable.
 *
 * Seeing when a detection came in is part of judging whether it is still worth
 * showing — a reference from four minutes ago is usually stale mid-sermon.
 */
function arrivalTime(createdAt: string): { label: string; iso: string } | null {
  const date = new Date(createdAt)
  if (Number.isNaN(date.getTime())) return null
  return {
    label: date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }),
    iso: createdAt,
  }
}

/**
 * A single reference tile. Pass `onSelect` to make it selectable: a pending row
 * takes the keyboard cursor that way (a click anywhere on the row but its buttons,
 * or focus, highlights it; Enter shows the highlighted row). History rows are not
 * selectable; they only have Show.
 *
 * The highlighted row takes a subtle tint and outline and its Show uses amber.
 *
 * Detection confidence is not a chip. A Low detection's row has a dashed edge;
 * Medium and High look the same. The word itself is the row's accessible description
 * and its tooltip.
 * A manual passage carries no confidence — it was never a detection — so its badge
 * names its source instead. The same goes for a passage follow-along showed by
 * itself ('follow').
 */
export function ReferenceCard({
  reference,
  selected = false,
  onSelect,
  showConfidence = true,
  removeAction,
  actions,
}: ReferenceCardProps) {
  const { t } = useI18n()
  const titleId = useId()
  const confidenceId = useId()
  const badgesId = useId()
  const selectable = onSelect !== undefined
  const time = arrivalTime(reference.created_at)
  const detected = reference.source !== 'follow' && reference.source !== 'manual'
  const confidence =
    showConfidence && detected && reference.confidence !== null
      ? `${t('ref.confidenceSr')}${t(CONFIDENCE_LABELS[reference.confidence])}`
      : null

  // The source badge: Followed, or Manual for a passage that was never a detection.
  const sourceBadge =
    reference.source === 'follow'
      ? 'follow'
      : reference.confidence === null || reference.source === 'manual'
        ? 'manual'
        : null

  // The start of the passage, so a row can be recognised before it is shown. A
  // detection from an older pipeline may carry only the per-verse list.
  const preview = reference.text || reference.verses?.[0]?.text || ''
  // The badges (source, Context) and the confidence word describe the row, when shown.
  const badged = sourceBadge !== null || reference.inferred
  const describedBy = [badged ? badgesId : null, confidence ? confidenceId : null]
    .filter(Boolean)
    .join(' ')

  return (
    <div
      className={`ref-item ${selected ? 'is-selected' : ''}`}
      data-cursor-row={selectable ? reference.id : undefined}
      data-confidence={confidence ? (reference.confidence ?? undefined) : undefined}
      title={confidence ?? undefined}
      // A click on the row's own buttons is theirs; anywhere else highlights the row.
      onClick={
        selectable
          ? event => {
              if (!(event.target as Element).closest('button')) onSelect()
            }
          : undefined
      }
    >
      <div
        className={`ref-card ${selected ? 'ref-card-selected' : ''}`}
        role={selectable ? 'button' : undefined}
        tabIndex={selectable ? 0 : undefined}
        aria-pressed={selectable ? selected : undefined}
        aria-label={
          selectable ? t('row.highlightAria', { ref: formatReference(reference) }) : undefined
        }
        aria-describedby={describedBy}
        // Focus is the keyboard's click: tabbing to a row highlights it, so Enter (the
        // console's own shortcut) shows the row that has focus.
        onFocus={onSelect}
        onKeyDown={
          selectable
            ? e => {
                if (e.key === ' ') {
                  e.preventDefault()
                  onSelect?.()
                }
              }
            : undefined
        }
      >
        <div className="ref-card-content">
          <div className="ref-card-header">
            <h3 className="ref-card-title" id={titleId}>
              {formatReference(reference)}
            </h3>
            <div className="ref-card-badges" id={badgesId}>
              {sourceBadge === 'follow' ? (
                <StatusBadge variant="info">
                  <span className="sr-only">{t('ref.sourceSr')}</span>
                  {t('ref.followed')}
                </StatusBadge>
              ) : sourceBadge === 'manual' ? (
                <StatusBadge variant="neutral">
                  <span className="sr-only">{t('ref.sourceSr')}</span>
                  {t('source.manual')}
                </StatusBadge>
              ) : null}
              {reference.inferred && <StatusBadge variant="info">{t('ref.context')}</StatusBadge>}
            </div>
          </div>

          {preview && <p className="ref-card-text">{preview}</p>}
          {confidence && (
            <span className="sr-only" id={confidenceId}>
              {confidence}
            </span>
          )}
        </div>
      </div>
      {/* Arrival order is the queue's order, and the clock reading on each card is
          what makes that order readable. Verse count is here too, so the operator
          knows what the display will be bounded to (`verses` is absent when talking
          to an older server). The row's buttons share the line, Show right-most. */}
      <div className="ref-item-foot">
        <div className="ref-card-meta">
          {time && (
            <time dateTime={time.iso} className="ref-card-time">
              {time.label}
            </time>
          )}
          {removeAction}
          {(reference.verses?.length ?? 0) > 1 && (
            <span className="ref-card-verse-count">
              {t('ref.verses', { count: reference.verses.length })}
            </span>
          )}
        </div>
        {actions && <div className="ref-item-actions">{actions(titleId)}</div>}
      </div>
    </div>
  )
}
