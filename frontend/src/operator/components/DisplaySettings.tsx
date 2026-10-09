import type { ReactNode } from 'react'
import {
  DEFAULT_VERSE_LIMIT,
  DISPLAY_THEME_OPTIONS,
  VERSE_LIMIT_OPTIONS,
} from '../../shared/verseText'
import type { DisplayTheme, VerseLimit } from '../../shared/verseText'

export interface DisplaySettingsProps {
  verseLimit: VerseLimit
  displayTheme: DisplayTheme
  /** Settings are server-owned, so they are read-only while disconnected. */
  disabled: boolean
  onVerseLimitChange: (next: VerseLimit) => void
  onThemeChange: (next: DisplayTheme) => void
  /** Controls that share this panel's styling, e.g. auto-approve. */
  children?: ReactNode
}

/**
 * Presentation controls for the audience display. The display page itself is
 * presentation-only: every change here is sent to the server, which pushes it
 * to all projectors.
 *
 * These are configuration, not moment-to-moment actions, so each one is a
 * single compact row and the whole block lives in the sidebar. Keeping it out of
 * the working column is what stops the live queue from being pushed off screen
 * on a short window.
 */
export function DisplaySettings({
  verseLimit,
  displayTheme,
  disabled,
  onVerseLimitChange,
  onThemeChange,
  children,
}: DisplaySettingsProps) {
  return (
    <div className="operator-display-settings">
      <div className="operator-setting">
        <span className="operator-setting-label">Verses shown</span>
        <div className="operator-verse-options" role="group" aria-label="Verses shown on display">
          {VERSE_LIMIT_OPTIONS.map(option => (
            <button
              key={option}
              type="button"
              className={`operator-verse-option ${verseLimit === option ? 'is-active' : ''}`}
              aria-pressed={verseLimit === option}
              disabled={disabled}
              onClick={() => onVerseLimitChange(option)}
            >
              {option}
            </button>
          ))}
          <button
            type="button"
            className={`operator-verse-option ${verseLimit === 'all' ? 'is-active' : ''}`}
            aria-pressed={verseLimit === 'all'}
            disabled={disabled}
            onClick={() => onVerseLimitChange('all')}
          >
            All
          </button>
        </div>
        <p className="operator-setting-hint">
          Default {DEFAULT_VERSE_LIMIT}. Longer passages shrink to fit.
        </p>
      </div>

      <div className="operator-setting">
        <span className="operator-setting-label">Display background</span>
        <div className="operator-verse-options" role="group" aria-label="Display background theme">
          {DISPLAY_THEME_OPTIONS.map(option => (
            <button
              key={option}
              type="button"
              className={`operator-verse-option ${displayTheme === option ? 'is-active' : ''}`}
              aria-pressed={displayTheme === option}
              disabled={disabled}
              onClick={() => onThemeChange(option)}
            >
              {option === 'dark' ? 'Dark' : 'Light'}
            </button>
          ))}
        </div>
        <p className="operator-setting-hint">Dark is easier on the eyes in a lit room.</p>
      </div>

      {children}
    </div>
  )
}
