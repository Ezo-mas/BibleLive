import { useId } from 'react'
import { useI18n } from '../../shared/i18nContext'
import type { Language, MessageKey } from '../../shared/i18n'

/**
 * The console's own language choices. `null` is "Auto": forget this browser's
 * choice, so the launcher's setting and then the browser's language decide.
 */
const CHOICES: { value: Language | null; labelKey: MessageKey }[] = [
  { value: 'en', labelKey: 'settings.interfaceEnglish' },
  { value: 'lt', labelKey: 'settings.interfaceLithuanian' },
  { value: null, labelKey: 'settings.interfaceAuto' },
]

/**
 * The language the console's text is shown in, chosen for this browser.
 *
 * Stored in this browser only, so another browser on the same computer keeps its
 * own. Choosing a language takes effect at once across the console, with no
 * reload; the audio pipeline and the Bible translation are not affected.
 */
export function InterfaceLanguageSettings() {
  const hintId = useId()
  const { t, override, setLanguage } = useI18n()

  return (
    <div className="operator-setting">
      <span className="operator-setting-label">{t('settings.interfaceLanguage')}</span>
      <div
        className="segmented"
        role="group"
        aria-label={t('settings.interfaceLanguage')}
        aria-describedby={hintId}
      >
        {CHOICES.map(choice => (
          <button
            key={choice.labelKey}
            type="button"
            className="segmented-option"
            aria-pressed={override === choice.value}
            onClick={() => setLanguage(choice.value)}
          >
            {t(choice.labelKey)}
          </button>
        ))}
      </div>
      <p className="operator-setting-hint" id={hintId}>
        {t('settings.interfaceHint')}
      </p>
    </div>
  )
}
