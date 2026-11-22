import { useId, useRef, useState } from 'react'
import type { ReactNode, Ref } from 'react'
import { Button, MonitorIcon, StatusBadge } from '../../components'
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
  /** The compact status uses the card as its scroll and focus target. */
  panelRef?: Ref<HTMLElement>
  /** What the server reports is on the projector right now. */
  reference: BibleReference | null
  /** True when the server confirms zero audience displays. */
  displayMissing?: boolean
  autoClearSeconds?: number | null
  verseLimit: VerseLimit
  displayTheme: DisplayTheme
  /** The picture the display shows while idle, shown here too while nothing is on screen. */
  idleImage?: IdleImage | null
  clearAction?: ReactNode
  expandAction?: ReactNode
  /** Find Scripture sits below the display actions in the stacked layout. */
  search?: ReactNode
  /** Laid over the top of the preview frame, taking no space (the follow-along strip). */
  overlay?: ReactNode
  /** Whether the audience display is on air; off air the preview keeps showing the passage. */
  live?: boolean
  /** Switch the display on or off air; omitted, the switch is not drawn. */
  onLiveChange?: (next: boolean) => void
  /** Server-held, so the switch is read-only while disconnected. */
  liveDisabled?: boolean
}

/**
 * ON DISPLAY: a miniature of the audience screen.
 *
 * Source of truth is the server (`current_display`, then `approval_confirmed`),
 * so this panel never guesses. The preview fills a frame that spans the card's width
 * and takes all its leftover height, edge to edge and with no bars around it, so it
 * is not always the projector's 16:9. It is drawn in the display's own theme with the display's own verse selection and
 * limit, so the operator can answer "what is on screen?" without walking over to
 * the projector. It is presentational only: nothing here sends anything.
 */
export function LivePanel({
  panelRef,
  reference,
  displayMissing = false,
  autoClearSeconds = null,
  verseLimit,
  displayTheme,
  idleImage = null,
  clearAction,
  expandAction,
  search,
  overlay,
  live = true,
  onLiveChange,
  liveDisabled = false,
}: LivePanelProps) {
  const { t } = useI18n()
  const displayWarningId = useId()
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
    <section
      className="operator-stage-panel operator-live"
      ref={panelRef}
      tabIndex={panelRef ? -1 : undefined}
      aria-label={t('stage.onDisplayAria')}
    >
      <div className="operator-card-head">
        <h2 className="operator-section-title">{t('stage.onDisplay')}</h2>
        {/* Shown by the server itself for follow-along, not picked by the operator. */}
        {reference?.source === 'follow' && (
          <StatusBadge variant="info" className="operator-live-tag">
            {t('ref.followed')}
          </StatusBadge>
        )}
        <div className="operator-live-actions">
          {expandAction}
          {clearAction}
          {live && reference && autoClearSeconds !== null && (
            <StatusBadge variant="neutral">
              {t('stage.autoClear', { seconds: autoClearSeconds })}
            </StatusBadge>
          )}
          {onLiveChange && (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className={`operator-live-switch${live ? ' is-live' : ''}`}
              // A state-named plain button, not an aria-pressed toggle: "Off air,
              // not pressed" would contradict the label. The title names the action.
              aria-label={t(live ? 'stage.liveOn' : 'stage.liveOff')}
              aria-describedby={displayMissing ? displayWarningId : undefined}
              disabled={liveDisabled}
              title={t(live ? 'stage.liveOffTitle' : 'stage.liveOnTitle')}
              icon={<span className="live-dot" aria-hidden="true" />}
              onClick={event => {
                // A double-click is one switch: once the server confirms the first click,
                // a second would flip it straight back (and re-show the passage).
                if (event.detail > 1) return
                onLiveChange(!live)
              }}
            >
              {/* Both labels reserve their width (pseudo-elements, so not text), keeping
                  Clear beside the switch still as it flips. */}
              <span
                className="operator-live-label"
                data-on={t('stage.liveOn')}
                data-off={t('stage.liveOff')}
              >
                <span>{t(live ? 'stage.liveOn' : 'stage.liveOff')}</span>
              </span>
            </Button>
          )}
          {displayMissing && (
            <span
              id={displayWarningId}
              className="operator-status-chip"
              data-testid="status-chip-no-display"
            >
              {t('stage.noDisplay')}
            </span>
          )}
        </div>
        {!live && <span className="sr-only">{t('stage.offAirNote')}</span>}
      </div>

      {search}

      <div className={`operator-monitor-wrap${live ? '' : ' is-off-air'}`}>
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
    </section>
  )
}
