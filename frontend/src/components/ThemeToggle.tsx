import { useTheme } from '../hooks/useTheme'
import type { Theme } from '../hooks/useTheme'
import { SunIcon, MoonIcon, MonitorIcon } from './Icons'
import './ThemeToggle.css'

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
  const theme = controlled ?? stored
  const selectTheme = onThemeChange ?? setTheme

  const options = [
    { value: 'light' as const, label: 'Light', icon: SunIcon },
    { value: 'dark' as const, label: 'Dark', icon: MoonIcon },
    { value: 'system' as const, label: 'System', icon: MonitorIcon },
  ]

  return (
    <div className="theme-toggle" role="radiogroup" aria-label="Theme selection">
      {options.map(({ value, label, icon: Icon }) => (
        <button
          key={value}
          type="button"
          role="radio"
          aria-checked={theme === value}
          aria-label={`${label} theme`}
          className={`theme-toggle-button ${theme === value ? 'active' : ''}`}
          onClick={() => selectTheme(value)}
          title={`${label} theme`}
        >
          <Icon className="theme-toggle-icon" aria-hidden="true" />
          <span className="theme-toggle-label">{label}</span>
        </button>
      ))}
    </div>
  )
}
