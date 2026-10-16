import { useId } from 'react'
import { AlertCircleIcon } from '../../components'
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
  const { version, available, error, isSwitching, change } = state
  const locked = disabled || isSwitching

  return (
    <div className="operator-setting">
      <div className="operator-setting-header">
        <span className="operator-setting-label">Bible version</span>
        <span className="operator-setting-value">
          {isSwitching ? 'Switching…' : version ? bibleVersionLabel(version) : ''}
        </span>
      </div>
      <div
        className="operator-verse-options"
        role="group"
        aria-label="Bible version"
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
        Follows the spoken language by default. A passage already on the display stays as it is.
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
