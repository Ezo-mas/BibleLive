import { AUTO_MODE_OPTIONS } from '../autoMode'
import type { AutoMode } from '../../shared/types'

export interface AutoApproveControlsProps {
  autoMode: AutoMode
  /** The mode is server-owned, so the selector is read-only while disconnected. */
  disabled: boolean
  onAutoModeChange: (next: AutoMode) => void
}

/**
 * Which newly detected references the server shows without waiting for the
 * operator. The server applies this only to detections that arrive after the
 * change, so switching it never drains the pending queue.
 */
export function AutoApproveControls({
  autoMode,
  disabled,
  onAutoModeChange,
}: AutoApproveControlsProps) {
  return (
    <>
      {/* Not a display setting, but it reuses the same controls so the
          console keeps one button style. */}
      <div className="operator-display-settings-header">
        <span className="operator-display-settings-label">Auto-approve</span>
        <span className="operator-display-settings-hint">
          Shows new detections automatically. Pending items stay put.
        </span>
      </div>
      <div className="operator-verse-options" role="group" aria-label="Auto-approve mode">
        {AUTO_MODE_OPTIONS.map(option => (
          <button
            key={option.value}
            type="button"
            className={`operator-verse-option ${autoMode === option.value ? 'is-active' : ''}`}
            aria-pressed={autoMode === option.value}
            disabled={disabled}
            onClick={() => onAutoModeChange(option.value)}
          >
            {option.label}
          </button>
        ))}
      </div>
    </>
  )
}
