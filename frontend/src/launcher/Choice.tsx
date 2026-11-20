import type { ReactNode } from 'react'
import type { Option } from './bridge'

interface Props {
  label: ReactNode
  value: Option['value']
  options: Option[]
  onChange: (value: Option['value']) => void
  disabled?: boolean
  errorId?: string
}

export function Choice({ label, value, options, onChange, disabled, errorId }: Props) {
  const choices = options.some(option => option.value === value)
    ? options
    : [{ value, label: value ?? '' }, ...options]
  // Keep the empty-string option distinct from the null (system default) option.
  const optionValue = (value: Option['value']) => (value === '' ? '\0' : (value ?? ''))
  return (
    <label className="bl-field">
      <span>{label}</span>
      <select
        value={optionValue(value)}
        onChange={event => onChange(choices[event.currentTarget.selectedIndex].value)}
        disabled={disabled}
        aria-invalid={errorId ? true : undefined}
        aria-describedby={errorId}
      >
        {choices.map((option, index) => (
          <option key={index} value={optionValue(option.value)}>
            {option.label}
          </option>
        ))}
      </select>
    </label>
  )
}
