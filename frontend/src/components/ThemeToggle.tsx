import { useTheme } from '../hooks/useTheme'
import { SunIcon, MoonIcon, MonitorIcon } from './Icons'
import './ThemeToggle.css'

export function ThemeToggle() {
  const { theme, setTheme } = useTheme()

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
          onClick={() => setTheme(value)}
          title={`${label} theme`}
        >
          <Icon className="theme-toggle-icon" aria-hidden="true" />
          <span className="theme-toggle-label">{label}</span>
        </button>
      ))}
    </div>
  )
}
