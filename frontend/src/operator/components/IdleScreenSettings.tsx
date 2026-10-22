import { useId, useRef } from 'react'
import { AlertCircleIcon, Button } from '../../components'
import { useI18n } from '../../shared/i18nContext'
import { IDLE_SCREEN_FITS, idleImageUrl } from '../../shared/idleScreen'
import type { IdleScreenState } from '../hooks/useIdleScreen'

export interface IdleScreenSettingsProps {
  state: IdleScreenState
  /** Settings are server-owned, so the controls are read-only while disconnected. */
  disabled: boolean
}

/** The file types the picker offers. The server checks the real format; this only narrows the list. */
const PICKER_ACCEPT = 'image/png,image/jpeg,image/webp'

/**
 * The picture shown on the audience display while nothing is on screen, as three
 * setting rows in the dialog's own anatomy: the picture (upload, replace, remove),
 * how it fits (Logo or Fill screen), and a 16:9 preview with the current state.
 *
 * It appears on the display only while the screen is idle, so the preview shows
 * the picture as the display will fit it. Uploading replaces the picture; Remove
 * puts the display back to blank.
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
    <>
      {/* The picture itself: upload, replace, remove. */}
      <div className="operator-setting">
        <div className="operator-setting-header">
          <span className="operator-setting-label">{t('idle.label')}</span>
        </div>
        <div className="operator-setting-control">
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
            disabled={locked}
            onClick={() => fileInputRef.current?.click()}
          >
            {picture ? t('idle.replace') : t('idle.upload')}
          </Button>
          <Button variant="danger" disabled={locked || !picture} onClick={remove}>
            {t('idle.remove')}
          </Button>
        </div>
        <p className="operator-setting-hint">{t('idle.hint')}</p>
        {error && (
          <p className="operator-scripture-error">
            <AlertCircleIcon aria-hidden="true" />
            {error}
          </p>
        )}
      </div>

      {/* How the picture fits the screen. */}
      <div className="operator-setting">
        <div className="operator-setting-header">
          <span className="operator-setting-label">{t('idle.fitLabel')}</span>
        </div>
        <div
          className="segmented"
          role="group"
          aria-label={t('idle.fitGroup')}
          aria-describedby={hintId}
        >
          {IDLE_SCREEN_FITS.map(fit => (
            <button
              key={fit}
              type="button"
              className="segmented-option"
              aria-pressed={info?.fit === fit}
              disabled={locked}
              onClick={() => changeFit(fit)}
            >
              {fit === 'fill' ? t('idle.fitFill') : t('idle.fitLogo')}
            </button>
          ))}
        </div>
        <p className="operator-setting-hint" id={hintId}>
          {t('idle.fitHint')}
        </p>
      </div>

      {/* What the display will show, in its own row, with the current state under it. */}
      <div className="operator-setting operator-setting-preview">
        <div
          className={`idle-screen-preview ${info?.fit === 'fill' ? 'idle-screen-preview--fill' : ''}`}
          data-testid="idle-screen-preview"
        >
          {picture ? (
            <img src={idleImageUrl(picture)} alt={t('idle.previewAlt')} />
          ) : (
            <span className="idle-screen-preview-blank">{t('idle.blank')}</span>
          )}
        </div>
        <p className="operator-setting-hint">
          {picture ? t('idle.stateImage', { fit: fitLabel }) : t('idle.stateBlank')}
        </p>
      </div>
    </>
  )
}
