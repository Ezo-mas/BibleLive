import { useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { MonitorIcon, StatusBadge } from '../../components'
import { useI18n } from '../../shared/i18nContext'
import { PanelHint } from './PanelHint'
import { usePreviewFit } from '../hooks/usePreviewFit'
import { formatReference } from '../../shared/reference'
import { hiddenVerseCount, selectVerses } from '../../shared/verseText'
import type { DisplayTheme, VerseLimit } from '../../shared/verseText'
import type { BibleReference } from '../../shared/types'
import { idleImageUrl } from '../../shared/idleScreen'
import type { IdleImage } from '../../shared/idleScreen'

export interface LivePanelProps {
  /** What the server reports is on the projector right now. */
  reference: BibleReference | null
  verseLimit: VerseLimit
  displayTheme: DisplayTheme
  /** The picture the display shows while idle, shown here too while nothing is on screen. */
  idleImage?: IdleImage | null
  /** The row of controls under the preview (Clear display, verses, auto-approve). */
  children?: ReactNode
  /** Laid over the top of the preview frame, taking no space (the follow-along strip). */
  overlay?: ReactNode
}

/**
 * ON DISPLAY: a miniature of the audience screen.
 *
 * Source of truth is the server (`current_display`, then `approval_confirmed`),
 * so this panel never guesses. The preview is the largest 16:9 box that fits a
 * neutral frame, which spans the card's width and takes all its leftover height; it is
 * centred in the frame (the frame shows as bars at the sides or above and below),
 * drawn in the display's own theme with the display's own verse selection and
 * limit, so the operator can answer "what is on screen?" without walking over to
 * the projector. It is presentational only: nothing here sends anything.
 */
export function LivePanel({
  reference,
  verseLimit,
  displayTheme,
  idleImage = null,
  children,
  overlay,
}: LivePanelProps) {
  const { t } = useI18n()
  // A picture the browser could not load falls back to the empty message, as the
  // display falls back to a blank screen; a new upload is a new version and a new try.
  const [brokenIdleVersion, setBrokenIdleVersion] = useState<string | null>(null)
  const shownIdleImage = idleImage && idleImage.version !== brokenIdleVersion ? idleImage : null
  const verses = selectVerses(reference, verseLimit)
  const hiddenCount = hiddenVerseCount(reference, verseLimit)
  const monitorRef = useRef<HTMLDivElement>(null)
  const blockRef = useRef<HTMLDivElement>(null)
  const innerRef = useRef<HTMLDivElement>(null)
  // Sized the way the audience display sizes it: shrunk until the verses fit.
  usePreviewFit(
    monitorRef,
    blockRef,
    innerRef,
    verses.reduce((sum, verse) => sum + verse.text.length, 0),
    verses.length,
    `${reference?.id ?? ''}|${verses.length}`
  )

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
            ref={monitorRef}
            data-theme={displayTheme}
            data-testid="live-stage"
            role="group"
            aria-label={t('stage.previewAria')}
          >
            {/* The block the verses are fitted to; they start at its top if they ever
                run past it, so the beginning of the passage is what shows. */}
            <div className="operator-live-fit" ref={blockRef}>
              <div className="operator-live-verses" data-testid="live-verses" ref={innerRef}>
                {verses.map(verse => (
                  <p className="operator-live-verse" key={verse.verse}>
                    <span className="operator-live-verse-number" aria-hidden="true">
                      {verse.verse}
                    </span>
                    <span className="operator-live-verse-text">{verse.text}</span>
                  </p>
                ))}
              </div>
            </div>

            <div className="operator-live-reference">{formatReference(reference)}</div>

            {hiddenCount > 0 && (
              <div className="operator-live-note">
                {t('stage.moreVerses', { count: hiddenCount })}
              </div>
            )}
          </div>
        ) : shownIdleImage ? (
          /* Idle with a picture: what the audience sees, the picture on the themed screen. */
          <div
            className="operator-monitor is-idle has-picture"
            data-theme={displayTheme}
            data-testid="live-idle-picture"
            role="group"
            aria-label={t('stage.previewAria')}
          >
            <img
              className={`operator-idle-image operator-idle-image--${shownIdleImage.fit}`}
              src={idleImageUrl(shownIdleImage)}
              alt=""
              onError={() => setBrokenIdleVersion(shownIdleImage.version)}
            />
            <span className="sr-only">{t('stage.nothingOnDisplay')}</span>
          </div>
        ) : (
          <div
            className="operator-monitor is-idle"
            data-theme={displayTheme}
            role="group"
            aria-label={t('stage.previewAria')}
          >
            <PanelHint
              icon={<MonitorIcon />}
              title={t('stage.nothingOnDisplay')}
              hint={t('stage.approvedHint')}
            />
          </div>
        )}
        {overlay}
      </div>

      {children}
    </section>
  )
}
