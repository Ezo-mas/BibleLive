import { useI18n } from '../shared/i18nContext'
import { useTheme } from '../hooks/useTheme'
import type { Theme } from '../hooks/useTheme'
import { SunIcon, MoonIcon, MonitorIcon } from './Icons'
import '../styles/controls.css'

export interface ThemeToggleProps {
  /**
   * The current setting, when the caller owns it. The operator console does:
   * the app theme has to be applied whether or not this control is on screen,
   * so `App` holds the hook and this component just renders and reports.
   */
  theme?: Theme
  onThemeChange?: (next: Theme) => void
}

export function ThemeToggle({ theme: controlled, onThemeChange }: ThemeToggleProps = {}) {
  // Uncontrolled by default, which is what the display page uses: there the
  // control is the only thing that sets the theme.
  const { theme: stored, setTheme } = useTheme()
  const { t } = useI18n()
  const theme = controlled ?? stored
  const selectTheme = onThemeChange ?? setTheme

  const options = [
    { value: 'light' as const, label: t('theme.light'), name: t('theme.lightAria'), icon: SunIcon },
    { value: 'dark' as const, label: t('theme.dark'), name: t('theme.darkAria'), icon: MoonIcon },
    {
      value: 'system' as const,
      label: t('theme.system'),
      name: t('theme.systemAria'),
      icon: MonitorIcon,
    },
  ]

  return (
    <div className="segmented" role="radiogroup" aria-label={t('theme.group')}>
      {options.map(({ value, label, name, icon: Icon }) => (
        <button
          key={value}
          type="button"
          role="radio"
          aria-checked={theme === value}
          aria-label={name}
          className="segmented-option"
          onClick={() => selectTheme(value)}
          title={name}
        >
          <Icon aria-hidden="true" />
          <span>{label}</span>
        </button>
      ))}
    </div>
  )
}
