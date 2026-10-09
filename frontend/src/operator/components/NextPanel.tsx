import { Button, BookIcon, CheckIcon, XIcon } from '../../components'
import { PanelHint } from './PanelHint'
import { formatReference } from '../../shared/reference'
import { hiddenVerseCount, selectVerses } from '../../shared/verseText'
import type { VerseLimit } from '../../shared/verseText'
import type { NextSource } from '../hooks/useOperatorState'
import type { BibleReference } from '../../shared/types'

export interface NextPanelProps {
  /** The staged scripture, or null when nothing is waiting. */
  reference: BibleReference | null
  /** Where the staged scripture came from; Show means different things for each. */
  source: NextSource
  verseLimit: VerseLimit
  /** False while the reference cannot be shown (stale, in flight, or socket closed). */
  canShow: boolean
  onShow: () => void
  onDiscard: () => void
}

/**
 * NEXT: the scripture being prepared for the projector.
 *
 * Staging is entirely client-side and changes nothing on screen — the display
 * only moves when the operator presses Show, which travels the normal approval
 * path (the server owns the display). Deliberately smaller and plainer than the
 * LIVE panel so a glance tells them apart.
 *
 * The badge names which of the two doors the staged scripture came through — a
 * queued detection or the operator's own search — because Show sends a different
 * request for each, and the operator should never have to guess which one they
 * are about to display.
 */
export function NextPanel({
  reference,
  source,
  verseLimit,
  canShow,
  onShow,
  onDiscard,
}: NextPanelProps) {
  const verses = selectVerses(reference, verseLimit)
  const hiddenCount = hiddenVerseCount(reference, verseLimit)

  return (
    <section className="operator-section operator-next" aria-label="Up next">
      <div className="operator-section-header">
        <h2 className="operator-section-title">Up Next</h2>
        <span
          className={`operator-next-badge ${source ? `is-${source}` : ''}`}
          data-source={source ?? 'none'}
        >
          {source === 'manual' ? 'Manual' : source === 'detected' ? 'Detected' : 'Staged'}
        </span>
      </div>

      <div className="operator-section-content">
        {reference ? (
          <div className="operator-next-body">
            <div className="operator-next-preview" data-testid="next-preview">
              <h3 className="operator-next-reference">{formatReference(reference)}</h3>
              <ol className="operator-next-verses" data-testid="next-verses">
                {verses.map(verse => (
                  <li className="operator-next-verse" key={verse.verse}>
                    <span className="operator-next-verse-number" aria-hidden="true">
                      {verse.verse}
                    </span>
                    <span className="operator-next-verse-text">{verse.text}</span>
                  </li>
                ))}
              </ol>
              {hiddenCount > 0 && (
                <div className="operator-next-note">
                  +{hiddenCount} more verse{hiddenCount === 1 ? '' : 's'} hidden by the current limit
                </div>
              )}
            </div>

            <div className="operator-next-actions">
              <Button
                variant="primary"
                size="md"
                icon={<CheckIcon />}
                onClick={onShow}
                disabled={!canShow}
              >
                Show
              </Button>
              <Button variant="ghost" size="md" icon={<XIcon />} onClick={onDiscard}>
                Discard
              </Button>
            </div>
          </div>
        ) : (
          <PanelHint
            icon={<BookIcon />}
            title="Nothing staged"
            hint="Select a reference in the queue, or find a passage, to preview it here."
          />
        )}
      </div>
    </section>
  )
}
