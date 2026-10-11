import type { AutoMode } from '../shared/types'

/**
 * Auto-approve options, most conservative first. The server owns this setting
 * (and persists it), so the console never stores it locally.
 *
 * Each description names the confidence levels that qualify, because the short
 * button labels ('Med+' and 'All') do not tell an operator which detections
 * will skip the queue. Every one of them says "new" on purpose: changing the
 * mode only affects detections that arrive afterwards, so anything already
 * waiting for approval stays where it is.
 */
export const AUTO_MODE_OPTIONS: { value: AutoMode; label: string; description: string }[] = [
  { value: 'off', label: 'Off', description: 'Every new detection waits for your approval.' },
  {
    value: 'high',
    label: 'High',
    description: 'New high-confidence detections go straight to the display.',
  },
  {
    value: 'medium_and_up',
    label: 'Med+',
    description: 'New high- and medium-confidence detections go straight to the display.',
  },
  {
    value: 'all',
    label: 'All',
    description: 'Every new detection goes straight to the display.',
  },
]

/** Coerce an auto mode from the server; an unknown value means manual approval. */
export function coerceAutoMode(value: unknown): AutoMode {
  const match = AUTO_MODE_OPTIONS.find(option => option.value === value)
  return match ? match.value : 'off'
}

/** The option for a mode, so callers can read its label or description. */
export function autoModeOption(value: AutoMode) {
  return AUTO_MODE_OPTIONS.find(option => option.value === value) ?? AUTO_MODE_OPTIONS[0]
}
