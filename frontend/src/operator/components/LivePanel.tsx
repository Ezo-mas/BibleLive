import type { ReactNode } from 'react'
import { MonitorIcon, StatusBadge } from '../../components'
import { useI18n } from '../../shared/i18nContext'
import { PanelHint } from './PanelHint'
import { previewFontSize } from '../previewFit'
import { formatReference } from '../../shared/reference'
import { hiddenVerseCount, selectVerses } from '../../shared/verseText'
import type { DisplayTheme, VerseLimit } from '../../shared/verseText'
import type { BibleReference } from '../../shared/types'

export interface LivePanelProps {
  /** What the server reports is on the projector right now. */
  reference: BibleReference | null
  verseLimit: VerseLimit
  displayTheme: DisplayTheme
  /** The row of controls under the stage (Clear Display, the auto-approve mode). */
  children?: ReactNode
}

/**
 * LIVE: the scripture the audience is looking at.
 *
 * Source of truth is the server (`current_display`, then `approval_confirmed`),
 * so this panel never guesses. It renders with the display's own verse
 * selection, verse limit and theme, so the operator can answer "what is on
 * screen?" without walking over to the projector.
 */
export function LivePanel({ reference, verseLimit, displayTheme, children }: LivePanelProps) {
  const { t } = useI18n()
  const verses = selectVerses(reference, verseLimit)
  const hiddenCount = hiddenVerseCount(reference, verseLimit)

  return (
    <section className="operator-stage-panel operator-live" aria-label={t('stage.onDisplayAria')}>
      <div className="operator-stage-header">
        <h2 className="operator-stage-title">{t('stage.onDisplay')}</h2>
        <span className={`operator-live-badge ${reference ? 'is-live' : ''}`}>
          <span className="operator-live-dot" aria-hidden="true" />
          {reference ? t('stage.live') : t('stage.idle')}
        </span>
      </div>

      {reference ? (
        /* The display theme scopes the colour tokens here, so the preview
           shows the background the projectors are actually using. */
        <div className="operator-stage" data-theme={displayTheme} data-testid="live-stage">
          <div
            className="operator-live-verses"
            data-testid="live-verses"
            style={{ fontSize: `${previewFontSize(verses)}px` }}
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

          <div className="operator-live-reference">
            {formatReference(reference)}
            {/* Shown by the server itself for follow-along, not picked by the operator. */}
            {reference.source === 'follow' && (
              <StatusBadge variant="info" className="operator-live-tag">
                {t('ref.followed')}
              </StatusBadge>
            )}
          </div>

          {hiddenCount > 0 && (
            <div className="operator-live-note">
              {t('stage.moreVerses', { count: hiddenCount })}
            </div>
          )}
        </div>
      ) : (
        <div className="operator-stage is-empty">
          <PanelHint
            icon={<MonitorIcon />}
            title={t('stage.nothingOnDisplay')}
            hint={t('stage.approvedHint')}
          />
        </div>
      )}

      {children}
    </section>
  )
}
