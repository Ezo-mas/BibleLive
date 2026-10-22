import type { ReactNode } from 'react'
import { MonitorIcon, StatusBadge } from '../../components'
import { useI18n } from '../../shared/i18nContext'
import { PanelHint } from './PanelHint'
import { previewFontCqw } from '../previewFit'
import { formatReference } from '../../shared/reference'
import { hiddenVerseCount, selectVerses } from '../../shared/verseText'
import type { DisplayTheme, VerseLimit } from '../../shared/verseText'
import type { BibleReference } from '../../shared/types'

export interface LivePanelProps {
  /** What the server reports is on the projector right now. */
  reference: BibleReference | null
  verseLimit: VerseLimit
  displayTheme: DisplayTheme
  /** The row of controls under the preview (Clear display, verses, auto-approve). */
  children?: ReactNode
}

/**
 * ON DISPLAY: a miniature of the audience screen.
 *
 * Source of truth is the server (`current_display`, then `approval_confirmed`),
 * so this panel never guesses. The preview is a 16:9 box that fills the card's
 * width and its leftover height (letterboxed only when the height is the limit),
 * drawn in the display's own theme with the display's own verse selection and
 * limit, so the operator can answer "what is on screen?" without walking over to
 * the projector. It is presentational only: nothing here sends anything.
 */
export function LivePanel({ reference, verseLimit, displayTheme, children }: LivePanelProps) {
  const { t } = useI18n()
  const verses = selectVerses(reference, verseLimit)
  const hiddenCount = hiddenVerseCount(reference, verseLimit)

  return (
    <section className="operator-stage-panel operator-live" aria-label={t('stage.onDisplayAria')}>
      <div className="operator-card-head">
        <h2 className="operator-stage-title">{t('stage.onDisplay')}</h2>
        {/* Shown by the server itself for follow-along, not picked by the operator. */}
        {reference?.source === 'follow' && (
          <StatusBadge variant="info" className="operator-live-tag">
            {t('ref.followed')}
          </StatusBadge>
        )}
      </div>

      <div className="operator-monitor-wrap">
        {reference ? (
          /* The display theme scopes the colour tokens here, so the preview
             shows the background the projectors are actually using. */
          <div
            className="operator-monitor"
            data-theme={displayTheme}
            data-testid="live-stage"
            aria-label={t('stage.previewAria')}
          >
            <div
              className="operator-live-verses"
              data-testid="live-verses"
              style={{ fontSize: `${previewFontCqw(verses)}cqw` }}
            >
              {verses.map(verse => (
                <p className="operator-live-verse" key={verse.verse}>
                  <span className="operator-live-verse-number" aria-hidden="true">
                    {verse.verse}
                  </span>
                  <span className="operator-live-verse-text">{verse.text}</span>
                </p>
              ))}
            </div>

            <div className="operator-live-reference">{formatReference(reference)}</div>

            {hiddenCount > 0 && (
              <div className="operator-live-note">
                {t('stage.moreVerses', { count: hiddenCount })}
              </div>
            )}
          </div>
        ) : (
          <div
            className="operator-monitor is-idle"
            data-theme={displayTheme}
            aria-label={t('stage.previewAria')}
          >
            <PanelHint
              icon={<MonitorIcon />}
              title={t('stage.nothingOnDisplay')}
              hint={t('stage.approvedHint')}
            />
          </div>
        )}
      </div>

      {children}
    </section>
  )
}
