import { spokenLanguageLabel } from '../languageApi'

export interface LanguageChipProps {
  /** The language the pipeline confirmed, or null while it is unknown. */
  language: string | null
  /** Open the settings dialog on the language control. */
  onOpenSettings: () => void
}

/**
 * The spoken language, at a glance, in the header.
 *
 * The pipeline listens for one language at a time, and a service in the other one
 * produces nonsense detections — so the operator should never have to open
 * anything to know which it is. The chip is a plain reading of the last confirmed
 * value (nothing is assumed while it is unknown) and opens Settings, where it is
 * changed.
 */
export function LanguageChip({ language, onOpenSettings }: LanguageChipProps) {
  if (!language) return null

  return (
    <button
      type="button"
      className="operator-language-chip"
      onClick={onOpenSettings}
      aria-haspopup="dialog"
      aria-label={`Spoken language: ${spokenLanguageLabel(language)}. Change in settings`}
      title="Spoken language — open settings"
    >
      <span className="operator-language-chip-label">Speech</span>
      <strong className="operator-language-chip-value">{language.toUpperCase()}</strong>
    </button>
  )
}
