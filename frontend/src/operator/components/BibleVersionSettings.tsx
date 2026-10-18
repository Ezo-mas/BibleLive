import { useId } from 'react'
import { AlertCircleIcon } from '../../components'
import { useI18n } from '../../shared/i18nContext'
import { bibleVersionLabel } from '../bibleVersionApi'
import type { BibleVersionState } from '../hooks/useBibleVersion'

export interface BibleVersionSettingsProps {
  state: BibleVersionState
  /** The switch is a server request, so the selector is read-only while disconnected. */
  disabled: boolean
}

/**
 * Which Bible translation the display shows.
 *
 * Switching the spoken language also moves to that language's default
 * translation, so this is for overriding that (or for reading English scripture
 * to a Lithuanian audience). Passages already on screen are left alone; the next
 * one shown, and anything waiting in the queue, uses the chosen translation.
 */
export function BibleVersionSettings({ state, disabled }: BibleVersionSettingsProps) {
  const hintId = useId()
  const { t } = useI18n()
  const { version, available, error, isSwitching, change } = state
  const locked = disabled || isSwitching

  return (
    <div className="operator-setting">
      <div className="operator-setting-header">
        <span className="operator-setting-label">{t('settings.bibleVersion')}</span>
        <span className="operator-setting-value">
          {isSwitching ? t('settings.switching') : version ? bibleVersionLabel(version, t) : ''}
        </span>
      </div>
      <div
        className="operator-verse-options"
        role="group"
        aria-label={t('settings.bibleVersion')}
        aria-describedby={hintId}
      >
        {available.map(id => (
          <button
            key={id}
            type="button"
            className={`operator-verse-option ${version === id ? 'is-active' : ''}`}
            aria-pressed={version === id}
            disabled={locked}
            onClick={() => change(id)}
          >
            {id.toUpperCase()}
          </button>
        ))}
      </div>
      <p className="operator-setting-hint" id={hintId}>
        {t('settings.versionHint')}
      </p>
      {error && (
        <p className="operator-scripture-error">
          <AlertCircleIcon aria-hidden="true" />
          {error}
        </p>
      )}
    </div>
  )
}
