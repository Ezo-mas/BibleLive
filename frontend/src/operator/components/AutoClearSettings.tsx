import { useEffect, useId, useState } from 'react'
import { Button } from '../../components'
import { useI18n } from '../../shared/i18nContext'

const MIN_SECONDS = 5
const MAX_SECONDS = 600
const DEFAULT_SECONDS = 60

export function AutoClearSettings({
  seconds,
  disabled,
  onChange,
}: {
  seconds: number | null
  disabled: boolean
  onChange: (next: number | null) => void
}) {
  const { t } = useI18n()
  const hintId = useId()
  const [draft, setDraft] = useState(String(seconds ?? DEFAULT_SECONDS))
  useEffect(() => {
    setDraft(String(seconds ?? DEFAULT_SECONDS))
  }, [seconds])
  const duration = Number(draft)
  const canSave =
    !disabled &&
    Number.isInteger(duration) &&
    duration >= MIN_SECONDS &&
    duration <= MAX_SECONDS &&
    duration !== seconds

  return (
    <form
      className="operator-setting"
      onSubmit={event => {
        event.preventDefault()
        if (canSave) onChange(duration)
      }}
    >
      <div className="operator-setting-text">
        <div className="operator-setting-header">
          <span className="operator-setting-label">{t('settings.autoClear')}</span>
          <span className="operator-setting-value">
            {seconds === null ? t('follow.off') : t('settings.autoClearValue', { seconds })}
          </span>
        </div>
        <p className="operator-setting-hint" id={hintId}>
          {t('settings.autoClearHint', { min: MIN_SECONDS, max: MAX_SECONDS })}
        </p>
      </div>
      <div className="operator-setting-control auto-clear-controls">
        <input
          className="input"
          type="number"
          min={MIN_SECONDS}
          max={MAX_SECONDS}
          step={1}
          value={draft}
          disabled={disabled}
          aria-label={t('settings.autoClearSeconds')}
          aria-describedby={hintId}
          onChange={event => setDraft(event.target.value)}
        />
        <Button variant="secondary" type="submit" disabled={!canSave}>
          {t('settings.autoClearSave')}
        </Button>
        <Button
          variant="ghost"
          type="button"
          disabled={disabled || seconds === null}
          onClick={() => onChange(null)}
        >
          {t('settings.autoClearOff')}
        </Button>
      </div>
    </form>
  )
}
