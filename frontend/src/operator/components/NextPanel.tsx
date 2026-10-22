import { BookIcon, Button, MonitorIcon } from '../../components'
import { useI18n } from '../../shared/i18nContext'
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
  /** True briefly after the Enter shortcut shows the staged detection. */
  flashing?: boolean
  onShow: () => void
  onDiscard: () => void
}

/**
 * UP NEXT: the scripture being prepared for the projector.
 *
 * Staging is entirely client-side and changes nothing on screen — the display
 * only moves when the operator presses Show on display, which travels the normal
 * approval path (the server owns the display). The block is deliberately not a
 * second preview: it is recessed, dashed ("queued, not live") and set apart from
 * the display preview above it, but it shares that card's anatomy: a header row
 * (label, reference, then Remove and Show on display at the right) and the verse
 * text below it, up to three lines.
 *
 * Show sends a different request for each door the staged scripture came through
 * — a queued detection, the operator's own search, or an entry from history — so
 * the source is kept in the markup for assistive technology and tests, even
 * though it is not drawn.
 *
 * The buttons are always present and disabled while there is nothing to show, so
 * the block keeps its size and the primary action is always where the operator's
 * eye expects it.
 */
export function NextPanel({
  reference,
  source,
  verseLimit,
  canShow,
  flashing = false,
  onShow,
  onDiscard,
}: NextPanelProps) {
  const { t } = useI18n()
  const verses = selectVerses(reference, verseLimit)
  const hiddenCount = hiddenVerseCount(reference, verseLimit)

  return (
    <section
      className="operator-next operator-card"
      aria-label={t('next.aria')}
      data-testid={reference ? 'next-preview' : undefined}
    >
      <div className="operator-card-head">
        {/* Label and reference are one group on the text baseline, centred as a
            group against the buttons. */}
        <div className="operator-card-title">
          <h2 className="operator-stage-title">{t('next.title')}</h2>
          {reference && <h3 className="operator-card-ref">{formatReference(reference)}</h3>}
          {hiddenCount > 0 && (
            <span className="operator-next-note">
              {t('next.moreHidden', { count: hiddenCount })}
            </span>
          )}
        </div>
        {/* Which door the staged scripture came through. It is kept for assistive
            technology and as a data attribute; the dashed edge and the label already
            say the block is queued, not live. */}
        <span
          className={`sr-only operator-next-badge ${source ? `is-${source}` : ''}`}
          data-source={source ?? 'none'}
        >
          {source === 'manual'
            ? t('source.manual')
            : source === 'history'
              ? t('source.history')
              : source === 'detected'
                ? t('source.detected')
                : t('source.staged')}
        </span>
        <span className="operator-card-grow" />
        <Button variant="secondary" onClick={onDiscard} disabled={reference === null}>
          {t('next.discard')}
        </Button>
        <Button
          variant="primary"
          className={flashing ? 'btn-flash' : ''}
          icon={<MonitorIcon />}
          onClick={onShow}
          disabled={!canShow}
        >
          {t('next.show')}
          {/* Enter shows a staged detection and nothing else, so the hint is only
              true — and only shown — for that source. */}
          {source === 'detected' && (
            <kbd className="operator-show-hint" aria-hidden="true">
              Enter
            </kbd>
          )}
        </Button>
      </div>

      {reference ? (
        <div className="operator-card-body">
          <p className="operator-card-verse" data-testid="next-verses">
            {verses.map(verse => (
              <span className="operator-next-verse" key={verse.verse}>
                <sup className="operator-card-verse-number" aria-hidden="true">
                  {verse.verse}
                </sup>
                <span className="operator-next-verse-text">{verse.text}</span>{' '}
              </span>
            ))}
          </p>
        </div>
      ) : (
        <div className="operator-card-body">
          <PanelHint
            icon={<BookIcon />}
            title={t('next.nothingStaged')}
            hint={t('next.stagedHint')}
          />
        </div>
      )}
    </section>
  )
}
