import { useId } from 'react'
import { AlertCircleIcon, Button } from '../../components'
import { useI18n } from '../../shared/i18nContext'
import { SPOKEN_LANGUAGES, spokenLanguageLabel } from '../languageApi'
import type { SpokenLanguageState } from '../hooks/useSpokenLanguage'

export interface LanguageSettingsProps {
  state: SpokenLanguageState
  /** The switch is a server request, so the selector is read-only while disconnected. */
  disabled: boolean
}

/**
 * The language the audio pipeline listens for: English or Lithuanian.
 *
 * It picks which speech model prompt and which reference parser the pipeline
 * uses, and takes effect straight away without restarting it. The pipeline is a
 * separate process, so this control only ever shows what it last confirmed: the
 * buttons wait while a switch is in flight, and when the server cannot reach the
 * pipeline they stay off and the reason is stated, with a way to ask again,
 * rather than offering a choice that cannot be made.
 *
 * Reuses the segmented control and row styling of the other settings, so the
 * dialog keeps one control vocabulary.
 */
export function LanguageSettings({ state, disabled }: LanguageSettingsProps) {
  const hintId = useId()
  const { t } = useI18n()
  const { language, status, error, isSwitching, refresh, change } = state
  const unavailable = status === 'unavailable'
  const locked = disabled || isSwitching || status !== 'ready'

  return (
    <div className="operator-setting">
      <div className="operator-setting-header">
        <span className="operator-setting-label">{t('settings.spokenLanguage')}</span>
        <span className="operator-setting-value">
          {isSwitching ? t('settings.switching') : language ? spokenLanguageLabel(language, t) : ''}
        </span>
      </div>
      <div
        className="segmented"
        role="group"
        aria-label={t('settings.spokenLanguage')}
        aria-describedby={hintId}
      >
        {SPOKEN_LANGUAGES.map(option => (
          <button
            key={option.code}
            type="button"
            className="segmented-option"
            aria-pressed={language === option.code}
            disabled={locked}
            onClick={() => change(option.code)}
          >
            {t(option.labelKey)}
          </button>
        ))}
      </div>

      {unavailable ? (
        <div className="operator-scripture-unavailable">
          {/* A standing condition, read when the dialog is opened — not an event to
              interrupt the operator with. A failed switch is announced by the
              console's own notice, so the line below does not repeat it as an
              alert either. */}
          <p className="operator-scripture-error">
            <AlertCircleIcon aria-hidden="true" />
            {error}
          </p>
          <Button variant="secondary" size="sm" onClick={refresh} disabled={disabled}>
            {t('settings.checkAgain')}
          </Button>
        </div>
      ) : (
        <>
          <p className="operator-setting-hint" id={hintId}>
            {status === 'loading' ? t('settings.checkingPipeline') : t('settings.takesEffect')}
          </p>
          {error && (
            <p className="operator-scripture-error">
              <AlertCircleIcon aria-hidden="true" />
              {error}
            </p>
          )}
        </>
      )}
    </div>
  )
}
