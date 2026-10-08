import { MonitorIcon } from '../../components'
import { PanelHint } from './PanelHint'
import { computeFitFontSize } from '../../display/fitText'
import { formatReference } from '../../shared/reference'
import { hiddenVerseCount, selectVerses } from '../../shared/verseText'
import type { DisplayTheme, VerseLimit } from '../../shared/verseText'
import type { BibleReference } from '../../shared/types'

/** Line-height multiplier applied to the fitted verse font size. */
const PREVIEW_LINE_HEIGHT = 1.35

/**
 * Nominal size of the preview stage, in px. The stage is sized to match in CSS;
 * the fit estimate is a guide, and the stage clips instead of scrolling (a
 * projector has no scrollbar either).
 */
const PREVIEW_WIDTH_PX = 620
const PREVIEW_HEIGHT_PX = 170
/** Reference line and gaps inside the preview. */
const PREVIEW_CHROME_PX = 44
/** Readability bounds for the miniature; the projector itself allows 16–88. */
const PREVIEW_MIN_FONT_PX = 12
const PREVIEW_MAX_FONT_PX = 20

export interface LivePanelProps {
  /** What the server reports is on the projector right now. */
  reference: BibleReference | null
  verseLimit: VerseLimit
  displayTheme: DisplayTheme
}

/**
 * LIVE: the scripture the audience is looking at.
 *
 * Source of truth is the server (`current_display`, then `approval_confirmed`),
 * so this panel never guesses. It renders with the display's own verse
 * selection, verse limit and theme, so the operator can answer "what is on
 * screen?" without walking over to the projector.
 */
export function LivePanel({ reference, verseLimit, displayTheme }: LivePanelProps) {
  const verses = selectVerses(reference, verseLimit)
  const hiddenCount = hiddenVerseCount(reference, verseLimit)

  const fontSize = Math.min(
    PREVIEW_MAX_FONT_PX,
    Math.max(
      PREVIEW_MIN_FONT_PX,
      computeFitFontSize({
        charCount: verses.reduce((sum, verse) => sum + verse.text.length, 0),
        lineCount: verses.length,
        widthPx: PREVIEW_WIDTH_PX,
        heightPx: PREVIEW_HEIGHT_PX + PREVIEW_CHROME_PX,
        lineHeight: PREVIEW_LINE_HEIGHT,
        chromePx: PREVIEW_CHROME_PX,
      })
    )
  )

  return (
    <section className="operator-section operator-live" aria-label="On display now">
      <div className="operator-section-header">
        <h2 className="operator-section-title">On Display</h2>
        <span className={`operator-live-badge ${reference ? 'is-live' : ''}`}>
          <span className="operator-live-dot" aria-hidden="true" />
          {reference ? 'Live' : 'Idle'}
        </span>
      </div>

      <div className="operator-section-content operator-live-content">
        {reference ? (
          /* The display theme scopes the colour tokens here, so the preview
             shows the background the projectors are actually using. */
          <div className="operator-live-stage" data-theme={displayTheme} data-testid="live-stage">
            <div
              className="operator-live-verses"
              data-testid="live-verses"
              style={{ fontSize: `${fontSize}px` }}
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
                +{hiddenCount} more verse{hiddenCount === 1 ? '' : 's'}
              </div>
            )}
          </div>
        ) : (
          <PanelHint
            icon={<MonitorIcon />}
            title="Nothing on display"
            hint="Approved references appear here and on the projector."
          />
        )}
      </div>
    </section>
  )
}
