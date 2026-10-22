import { useId, useRef } from 'react'
import { AlertCircleIcon, Button } from '../../components'
import { useI18n } from '../../shared/i18nContext'
import { IDLE_SCREEN_FITS, idleImageUrl } from '../../shared/idleScreen'
import type { IdleScreenState } from '../hooks/useIdleScreen'
import './IdleScreenSettings.css'

export interface IdleScreenSettingsProps {
  state: IdleScreenState
  /** Settings are server-owned, so the controls are read-only while disconnected. */
  disabled: boolean
}

/** The file types the picker offers. The server checks the real format; this only narrows the list. */
const PICKER_ACCEPT = 'image/png,image/jpeg,image/webp'

/**
 * The picture shown on the audience display while nothing is on screen.
 *
 * It appears on the display only while the screen is idle, so the preview here shows
 * the picture as the display will fit it: a 16:9 box in the chosen mode. Uploading
 * replaces the picture; Remove puts the display back to blank.
 */
export function IdleScreenSettings({ state, disabled }: IdleScreenSettingsProps) {
  const hintId = useId()
  const fileInputRef = useRef<HTMLInputElement>(null)
  const { t } = useI18n()
  const { info, error, isBusy, upload, changeFit, remove } = state
  const locked = disabled || isBusy
  const picture = info?.image ?? null
  const fitLabel = info?.fit === 'fill' ? t('idle.fitFill') : t('idle.fitLogo')

  return (
    <div className="operator-setting idle-screen-settings">
      <div className="operator-setting-header">
        <span className="operator-setting-label">{t('idle.label')}</span>
        <span className="operator-setting-value">
          {picture ? t('idle.stateImage', { fit: fitLabel }) : t('idle.stateBlank')}
        </span>
      </div>

      <div
        className={`idle-screen-preview ${info?.fit === 'fill' ? 'idle-screen-preview--fill' : ''}`}
        data-testid="idle-screen-preview"
      >
        {picture ? (
          <img src={idleImageUrl(picture)} alt={t('idle.previewAlt')} />
        ) : (
          <span className="operator-setting-hint">{t('idle.blank')}</span>
        )}
      </div>

      <div
        className="operator-verse-options"
        role="group"
        aria-label={t('idle.fitGroup')}
        aria-describedby={hintId}
      >
        {IDLE_SCREEN_FITS.map(fit => (
          <button
            key={fit}
            type="button"
            className={`operator-verse-option ${info?.fit === fit ? 'is-active' : ''}`}
            aria-pressed={info?.fit === fit}
            disabled={locked}
            onClick={() => changeFit(fit)}
          >
            {fit === 'fill' ? t('idle.fitFill') : t('idle.fitLogo')}
          </button>
        ))}
      </div>

      <div className="idle-screen-actions">
        <input
          ref={fileInputRef}
          type="file"
          accept={PICKER_ACCEPT}
          hidden
          data-testid="idle-screen-file"
          onChange={event => {
            const file = event.target.files?.[0]
            // Cleared so picking the same file again still reports a change.
            event.target.value = ''
            if (file) upload(file)
          }}
        />
        <Button
          variant="secondary"
          size="sm"
          disabled={locked}
          onClick={() => fileInputRef.current?.click()}
        >
          {picture ? t('idle.replace') : t('idle.upload')}
        </Button>
        <Button variant="ghost" size="sm" disabled={locked || !picture} onClick={remove}>
          {t('idle.remove')}
        </Button>
      </div>

      <p className="operator-setting-hint" id={hintId}>
        {t('idle.fitHint')}
      </p>
      <p className="operator-setting-hint">{t('idle.hint')}</p>
      {error && (
        <p className="operator-scripture-error">
          <AlertCircleIcon aria-hidden="true" />
          {error}
        </p>
      )}
    </div>
  )
}
