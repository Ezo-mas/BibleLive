import type { AutoMode } from '../shared/types'
import type { MessageKey } from '../shared/i18n'

/**
 * Auto-approve options, most conservative first. The server owns this setting
 * (and persists it), so the console never stores it locally.
 *
 * Each description names the confidence levels that qualify, because the short
 * button labels ('Med+' and 'All') do not tell an operator which detections
 * will skip the queue. Every one of them says "new" on purpose: changing the
 * mode only affects detections that arrive afterwards, so anything already
 * waiting for approval stays where it is.
 *
 * Labels are message keys, so the buttons and the explanation follow the language.
 */
export const AUTO_MODE_OPTIONS: {
  value: AutoMode
  labelKey: MessageKey
  descriptionKey: MessageKey
}[] = [
  { value: 'off', labelKey: 'auto.off', descriptionKey: 'auto.offDesc' },
  { value: 'high', labelKey: 'auto.high', descriptionKey: 'auto.highDesc' },
  { value: 'medium_and_up', labelKey: 'auto.mediumAndUp', descriptionKey: 'auto.mediumAndUpDesc' },
  { value: 'all', labelKey: 'auto.all', descriptionKey: 'auto.allDesc' },
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
