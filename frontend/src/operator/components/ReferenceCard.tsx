import { formatReference } from '../../shared/reference'
import { CheckIcon, StatusBadge } from '../../components'
import type { BibleReference } from '../../shared/types'

export interface ReferenceCardProps {
  reference: BibleReference
  selected?: boolean
  onSelect?: () => void
}

/**
 * A single reference tile. Pass `onSelect` to make it selectable — the pending
 * queue does, history does not, since history items are not approvable.
 */
export function ReferenceCard({ reference, selected = false, onSelect }: ReferenceCardProps) {
  const selectable = onSelect !== undefined

  const confidenceVariant =
    reference.confidence === 'high'
      ? 'success'
      : reference.confidence === 'medium'
        ? 'warning'
        : 'danger'

  return (
    <div
      className={`ref-card ${selected ? 'ref-card-selected' : ''}`}
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
            <StatusBadge variant={confidenceVariant}>{reference.confidence}</StatusBadge>
            {reference.inferred && <StatusBadge variant="info">Context</StatusBadge>}
          </div>
        </div>

        {reference.text && <p className="ref-card-text">{reference.text}</p>}

        {/* Verse count, so the operator knows what the display will be bounded to.
            `verses` is absent when talking to an older server. */}
        {(reference.verses?.length ?? 0) > 1 && (
          <div className="ref-card-meta">{reference.verses.length} verses</div>
        )}
      </div>
    </div>
  )
}
