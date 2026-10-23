import type { ReactNode } from 'react'
import {
  DEFAULT_VERSE_LIMIT,
  DISPLAY_THEME_OPTIONS,
  VERSE_LIMIT_OPTIONS,
} from '../../shared/verseText'
import type { DisplayTheme, VerseLimit } from '../../shared/verseText'
import { SegmentedOption } from '../../components'
import { useI18n } from '../../shared/i18nContext'

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
  const { t } = useI18n()
  return (
    <>
      <div className="operator-setting">
        <div className="operator-setting-text">
          <span className="operator-setting-label">{t('settings.verses')}</span>
          <p className="operator-setting-hint">
            {t('settings.versesDefault', { count: DEFAULT_VERSE_LIMIT })}
          </p>
        </div>
        <div className="segmented" role="group" aria-label={t('settings.versesGroup')}>
          {VERSE_LIMIT_OPTIONS.map(option => (
            <SegmentedOption
              key={option}
              aria-pressed={verseLimit === option}
              disabled={disabled}
              onClick={() => onVerseLimitChange(option)}
            >
              {option}
            </SegmentedOption>
          ))}
          <SegmentedOption
            aria-pressed={verseLimit === 'all'}
            disabled={disabled}
            onClick={() => onVerseLimitChange('all')}
          >
            {t('settings.versesAll')}
          </SegmentedOption>
        </div>
      </div>

      <div className="operator-setting">
        <div className="operator-setting-text">
          <span className="operator-setting-label">{t('settings.background')}</span>
          <p className="operator-setting-hint">{t('settings.bgHint')}</p>
        </div>
        <div className="segmented" role="group" aria-label={t('settings.backgroundGroup')}>
          {DISPLAY_THEME_OPTIONS.map(option => (
            <SegmentedOption
              key={option}
              aria-pressed={displayTheme === option}
              disabled={disabled}
              onClick={() => onThemeChange(option)}
            >
              {option === 'dark' ? t('settings.bgDark') : t('settings.bgLight')}
            </SegmentedOption>
          ))}
        </div>
      </div>

      {children}
    </>
  )
}
