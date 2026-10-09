import { formatReference } from '../../shared/reference'
import { CheckIcon, StatusBadge } from '../../components'
import type { BibleReference } from '../../shared/types'

export interface ReferenceCardProps {
  reference: BibleReference
  selected?: boolean
  onSelect?: () => void
}

/** Confidence in plain words; the badge is uppercased by the shared styles. */
const CONFIDENCE_LABELS: Record<'high' | 'medium' | 'low', string> = {
  high: 'High',
  medium: 'Medium',
  low: 'Low',
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
 * A single reference tile. Pass `onSelect` to make it selectable — the pending
 * queue does, history does not, since history items are not approvable.
 *
 * A selectable tile that is selected is the reference staged in Up Next, so the
 * card says so: the queue and the staging panel must never be confused for one
 * another during a live service.
 *
 * A manual passage carries no confidence — it was never a detection — so its
 * badge names its source instead of implying a confidence it does not have.
 */
export function ReferenceCard({ reference, selected = false, onSelect }: ReferenceCardProps) {
  const selectable = onSelect !== undefined
  const time = arrivalTime(reference.created_at)

  const confidenceVariant =
    reference.confidence === 'high'
      ? 'success'
      : reference.confidence === 'medium'
        ? 'warning'
        : 'danger'

  return (
    <div
      className={`ref-card ${selected ? 'ref-card-selected' : ''}`}
      data-confidence={reference.confidence ?? 'manual'}
      role={selectable ? 'button' : undefined}
      tabIndex={selectable ? 0 : undefined}
      aria-pressed={selectable ? selected : undefined}
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
      {selected && (
        <div className="ref-card-check" aria-hidden="true">
          <CheckIcon />
        </div>
      )}

      <div className="ref-card-content">
        <div className="ref-card-header">
          <h3 className="ref-card-title">{formatReference(reference)}</h3>
          <div className="ref-card-badges">
            {reference.confidence !== null && reference.source !== 'manual' ? (
              <StatusBadge variant={confidenceVariant}>
                <span className="sr-only">Detection confidence: </span>
                {CONFIDENCE_LABELS[reference.confidence]}
              </StatusBadge>
            ) : (
              <StatusBadge variant="neutral">
                <span className="sr-only">Source: </span>
                Manual
              </StatusBadge>
            )}
            {reference.inferred && <StatusBadge variant="info">Context</StatusBadge>}
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
            <span className="ref-card-verse-count">{reference.verses.length} verses</span>
          )}
          {selected && <span className="ref-card-staged">Staged in Up Next</span>}
        </div>
      </div>
    </div>
  )
}
