import type { AutoMode } from '../shared/types'

/**
 * Auto-approve options, most conservative first. The server owns this setting
 * (and persists it), so the console never stores it locally.
 */
export const AUTO_MODE_OPTIONS: { value: AutoMode; label: string }[] = [
  { value: 'off', label: 'Off' },
  { value: 'high', label: 'High' },
  { value: 'medium_and_up', label: 'Med+' },
  { value: 'all', label: 'All' },
]

/** Coerce an auto mode from the server; an unknown value means manual approval. */
export function coerceAutoMode(value: unknown): AutoMode {
  const match = AUTO_MODE_OPTIONS.find(option => option.value === value)
  return match ? match.value : 'off'
}
