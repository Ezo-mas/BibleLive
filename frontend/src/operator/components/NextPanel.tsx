import { Button, MonitorIcon, BookIcon } from '../../components'
import { PanelHint } from './PanelHint'
import { previewFontSize } from '../previewFit'
import { formatReference } from '../../shared/reference'
import { hiddenVerseCount, selectVerses } from '../../shared/verseText'
import type { DisplayTheme, VerseLimit } from '../../shared/verseText'
import type { NextSource } from '../hooks/useOperatorState'
import type { BibleReference } from '../../shared/types'

export interface NextPanelProps {
  /** The staged scripture, or null when nothing is waiting. */
  reference: BibleReference | null
  /** Where the staged scripture came from; Show means different things for each. */
  source: NextSource
  verseLimit: VerseLimit
  /** The projector's background, so the preview is drawn the way it will appear. */
  displayTheme: DisplayTheme
  /** False while the reference cannot be shown (stale, in flight, or socket closed). */
  canShow: boolean
  /** True briefly after the Enter shortcut shows the staged detection. */
  flashing?: boolean
  onShow: () => void
  onDiscard: () => void
}

/**
 * NEXT: the scripture being prepared for the projector.
 *
 * Staging is entirely client-side and changes nothing on screen — the display
 * only moves when the operator presses Show on display, which travels the normal
 * approval path (the server owns the display). The stage is drawn like the LIVE
 * one, in the projector's own colours, so what the operator is about to show is
 * what they see here; the two are told apart by their labels and by the one
 * filled button under this one.
 *
 * The label names which door the staged scripture came through — a queued
 * detection, the operator's own search, or an entry from history — because Show
 * sends a different request for each, and the operator should never have to
 * guess which one they are about to display.
 *
 * The buttons are always present and disabled while there is nothing to show, so
 * the row keeps its place and the primary action is always where the operator's
 * eye expects it.
 */
export function NextPanel({
  reference,
  source,
  verseLimit,
  displayTheme,
  canShow,
  flashing = false,
  onShow,
  onDiscard,
}: NextPanelProps) {
  const verses = selectVerses(reference, verseLimit)
  const hiddenCount = hiddenVerseCount(reference, verseLimit)

  return (
    <section className="operator-stage-panel operator-next" aria-label="Up next">
      <div className="operator-stage-header">
        <h2 className="operator-stage-title">Up Next</h2>
        <span
          className={`operator-next-badge ${source ? `is-${source}` : ''}`}
          data-source={source ?? 'none'}
        >
          {source === 'manual'
            ? 'Manual'
            : source === 'history'
              ? 'History'
              : source === 'detected'
                ? 'Detected'
                : 'Staged'}
        </span>
      </div>

      {reference ? (
        <div
          className="operator-stage operator-next-preview"
          data-theme={displayTheme}
          data-testid="next-preview"
        >
          <h3 className="operator-next-reference">{formatReference(reference)}</h3>
          <ol
            className="operator-next-verses"
            data-testid="next-verses"
            style={{ fontSize: `${previewFontSize(verses)}px` }}
          >
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
      ) : (
        <div className="operator-stage is-empty">
          <PanelHint
            icon={<BookIcon />}
            title="Nothing staged"
            hint="Select a reference in the queue, or find a passage, to preview it here."
          />
        </div>
      )}

      <div className="operator-stage-footer">
        <Button variant="ghost" size="md" onClick={onDiscard} disabled={reference === null}>
          Discard
        </Button>
        <Button
          variant="primary"
          size="md"
          className={`operator-show-button ${flashing ? 'btn-flash' : ''}`}
          icon={<MonitorIcon />}
          onClick={onShow}
          disabled={!canShow}
        >
          Show on display
          {/* Enter shows a staged detection and nothing else, so the hint is only
              true — and only shown — for that source. */}
          {source === 'detected' && (
            <kbd className="operator-show-hint" aria-hidden="true">
              Enter
            </kbd>
          )}
        </Button>
      </div>
    </section>
  )
}
