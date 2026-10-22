import { useId } from 'react'
import { useI18n } from '../../shared/i18nContext'

export interface FollowAutoControlsProps {
  followAuto: boolean
  /** The switch is server-owned, so it is read-only while disconnected. */
  disabled: boolean
  onFollowAutoChange: (next: boolean) => void
}

/**
 * Whether follow-along shows the verses a speaker reads on to by itself. It sits
 * with the auto-approve mode because it is the same kind of setting: the server
 * holds it, applies it to every console, and only transcripts that arrive after
 * the change are affected.
 *
 * Drawn like the auto-approve selector, with the same row and button styling, so
 * the settings keep one control vocabulary.
 */
export function FollowAutoControls({
  followAuto,
  disabled,
  onFollowAutoChange,
}: FollowAutoControlsProps) {
  const descriptionId = useId()
  const { t } = useI18n()

  return (
    <div className="operator-setting">
      <div className="operator-setting-header">
        <span className="operator-setting-label">{t('settings.followAuto')}</span>
        <span className={`operator-setting-value ${followAuto ? 'is-on' : ''}`}>
          {followAuto ? t('follow.on') : t('follow.off')}
        </span>
      </div>
      <div
        className="operator-verse-options"
        role="group"
        aria-label={t('settings.followAuto')}
        aria-describedby={descriptionId}
      >
        <button
          type="button"
          className={`operator-verse-option ${followAuto ? '' : 'is-active'}`}
          aria-pressed={!followAuto}
          disabled={disabled}
          onClick={() => onFollowAutoChange(false)}
        >
          {t('follow.off')}
        </button>
        <button
          type="button"
          className={`operator-verse-option ${followAuto ? 'is-active' : ''}`}
          aria-pressed={followAuto}
          disabled={disabled}
          onClick={() => onFollowAutoChange(true)}
        >
          {t('follow.on')}
        </button>
      </div>
      <p className="operator-setting-hint" id={descriptionId}>
        {t('settings.followAutoHint')}
      </p>
    </div>
  )
}
