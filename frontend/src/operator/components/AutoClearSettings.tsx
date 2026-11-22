import { useEffect, useId, useState } from 'react'
import { Button, SegmentedOption } from '../../components'
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
  const toggleHintId = useId()
  const hintId = useId()
  const durationId = useId()
  const enabled = seconds !== null
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
    <>
      <div className="operator-setting">
        <div className="operator-setting-text">
          <span className="operator-setting-label">{t('settings.autoClear')}</span>
          <p className="operator-setting-hint" id={toggleHintId}>
            {t('settings.autoClearHint')}
          </p>
        </div>
        <div
          className="segmented"
          role="group"
          aria-label={t('settings.autoClear')}
          aria-describedby={toggleHintId}
        >
          <SegmentedOption
            aria-pressed={!enabled}
            disabled={disabled}
            onClick={() => onChange(null)}
          >
            {t('follow.off')}
          </SegmentedOption>
          <SegmentedOption
            aria-pressed={enabled}
            disabled={disabled}
            onClick={() => onChange(seconds ?? DEFAULT_SECONDS)}
          >
            {t('follow.on')}
          </SegmentedOption>
        </div>
      </div>
      {enabled && (
        <form
          className="operator-setting"
          onSubmit={event => {
            event.preventDefault()
            if (canSave) onChange(duration)
          }}
        >
          <div className="operator-setting-text">
            <label className="operator-setting-label" htmlFor={durationId}>
              {t('settings.autoClearSeconds')}
            </label>
            <p className="operator-setting-hint" id={hintId}>
              {t('settings.autoClearRange', { min: MIN_SECONDS, max: MAX_SECONDS })}
            </p>
          </div>
          <div className="operator-setting-control auto-clear-controls">
            <input
              id={durationId}
              className="input"
              type="number"
              min={MIN_SECONDS}
              max={MAX_SECONDS}
              step={1}
              value={draft}
              disabled={disabled}
              aria-describedby={hintId}
              onChange={event => setDraft(event.target.value)}
            />
            <Button variant="secondary" type="submit" disabled={!canSave}>
              {t('settings.autoClearSave')}
            </Button>
          </div>
        </form>
      )}
    </>
  )
}
