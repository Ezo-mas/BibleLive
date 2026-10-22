import { formatReference } from '../../shared/reference'
import { StatusBadge } from '../../components'
import { useI18n } from '../../shared/i18nContext'
import type { MessageKey } from '../../shared/i18n'
import type { BibleReference } from '../../shared/types'

export interface ReferenceCardProps {
  reference: BibleReference
  selected?: boolean
  onSelect?: () => void
  /**
   * What selecting this card does, for screen readers. Without it the accessible
   * name is the card's whole text (reference, verse text, and meta), which is
   * long and does not say what pressing it does.
   */
  ariaLabel?: string
}

/** Confidence in plain words, as message keys. */
const CONFIDENCE_LABELS: Record<'high' | 'medium' | 'low', MessageKey> = {
  high: 'ref.confidence.high',
  medium: 'ref.confidence.medium',
  low: 'ref.confidence.low',
}

/**
 * The same confidence as a three-pip meter. Shape carries the level, so it reads
 * without colour; the word beside it is what a screen reader gets.
 */
const CONFIDENCE_PIPS: Record<'high' | 'medium' | 'low', string> = {
  high: '●●●',
  medium: '●●○',
  low: '●○○',
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
 * A single reference tile. Pass `onSelect` to make it selectable: the pending
 * queue stages an approvable detection that way, and history stages an entry to
 * show again.
 *
 * A selectable tile that is selected is the reference staged in Up Next, so the
 * card says so: the queue and the staging panel must never be confused for one
 * another during a live service.
 *
 * A manual passage carries no confidence — it was never a detection — so its
 * badge names its source instead of implying a confidence it does not have. The
 * same goes for a passage follow-along showed by itself ('follow').
 */
export function ReferenceCard({
  reference,
  selected = false,
  onSelect,
  ariaLabel,
}: ReferenceCardProps) {
  const { t } = useI18n()
  const selectable = onSelect !== undefined
  const time = arrivalTime(reference.created_at)

  return (
    <div
      className={`ref-card ${selected ? 'ref-card-selected' : ''}`}
      data-confidence={reference.confidence ?? 'manual'}
      role={selectable ? 'button' : undefined}
      tabIndex={selectable ? 0 : undefined}
      aria-pressed={selectable ? selected : undefined}
      aria-label={selectable ? ariaLabel : undefined}
      onClick={onSelect}
      onKeyDown={
        selectable
          ? e => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault()
                onSelect?.()
              }
            }
          : undefined
      }
    >
      <div className="ref-card-content">
        <div className="ref-card-header">
          <h3 className="ref-card-title">{formatReference(reference)}</h3>
          <div className="ref-card-badges">
            {reference.source === 'follow' ? (
              <StatusBadge variant="info">
                <span className="sr-only">{t('ref.sourceSr')}</span>
                {t('ref.followed')}
              </StatusBadge>
            ) : reference.confidence !== null && reference.source !== 'manual' ? (
              <span className="ref-card-confidence" data-confidence={reference.confidence}>
                <span className="ref-card-pips" aria-hidden="true">
                  {CONFIDENCE_PIPS[reference.confidence]}
                </span>
                <span className="sr-only">{t('ref.confidenceSr')}</span>
                {t(CONFIDENCE_LABELS[reference.confidence])}
              </span>
            ) : (
              <StatusBadge variant="neutral">
                <span className="sr-only">{t('ref.sourceSr')}</span>
                {t('source.manual')}
              </StatusBadge>
            )}
            {reference.inferred && <StatusBadge variant="info">{t('ref.context')}</StatusBadge>}
          </div>
        </div>

        {reference.text && <p className="ref-card-text">{reference.text}</p>}

        {/* Arrival order is the queue's order, and the clock reading on each card
            is what makes that order readable. Verse count is here too, so the
            operator knows what the display will be bounded to. `verses` is absent
            when talking to an older server. */}
        <div className="ref-card-meta">
          {time && (
            <time dateTime={time.iso} className="ref-card-time">
              {time.label}
            </time>
          )}
          {(reference.verses?.length ?? 0) > 1 && (
            <span className="ref-card-verse-count">
              {t('ref.verses', { count: reference.verses.length })}
            </span>
          )}
          {selected && <span className="ref-card-staged">{t('ref.staged')}</span>}
        </div>
      </div>
    </div>
  )
}
