import type { ReactNode } from 'react'
import { DISPLAY_THEME_OPTIONS } from '../../shared/verseText'
import type { DisplayTheme } from '../../shared/verseText'
import type { ClearMode } from '../../shared/types'
import { SegmentedOption } from '../../components'
import { useI18n } from '../../shared/i18nContext'

const CLEAR_MODE_OPTIONS = ['idle', 'blank'] as const

export interface DisplaySettingsProps {
  displayTheme: DisplayTheme
  clearMode: ClearMode
  /** Settings are server-owned, so they are read-only while disconnected. */
  disabled: boolean
  onThemeChange: (next: DisplayTheme) => void
  onClearModeChange: (next: ClearMode) => void
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
  displayTheme,
  clearMode,
  disabled,
  onThemeChange,
  onClearModeChange,
  children,
}: DisplaySettingsProps) {
  const { t } = useI18n()
  return (
    <>
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

      <div className="operator-setting">
        <div className="operator-setting-text">
          <span className="operator-setting-label">{t('settings.clearMode')}</span>
          <p className="operator-setting-hint">{t('settings.clearHint')}</p>
        </div>
        <div className="segmented" role="group" aria-label={t('settings.clearMode')}>
          {CLEAR_MODE_OPTIONS.map(option => (
            <SegmentedOption
              key={option}
              aria-pressed={clearMode === option}
              disabled={disabled}
              onClick={() => onClearModeChange(option)}
            >
              {option === 'idle' ? t('settings.clearIdle') : t('settings.clearBlank')}
            </SegmentedOption>
          ))}
        </div>
      </div>

      {children}
    </>
  )
}
