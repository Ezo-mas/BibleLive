import { useId } from 'react'
import { SegmentedOption } from '../../components'
import { useI18n } from '../../shared/i18nContext'
import { AUTO_MODE_OPTIONS, autoModeOption } from '../autoMode'
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
 *
 * The short button labels ('Med+' and 'All') do not say which detections
 * qualify, so the active mode is repeated as a value next to the label and as a
 * sentence under the buttons. Both come from the same option table the buttons
 * do, so the explanation cannot drift from the mode being sent.
 */
export function AutoApproveControls({
  autoMode,
  disabled,
  onAutoModeChange,
}: AutoApproveControlsProps) {
  const descriptionId = useId()
  const { t } = useI18n()
  const active = autoModeOption(autoMode)

  return (
    <div className="operator-setting">
      {/* Not a display setting, but it reuses the same row and button styling so
          the console keeps one control vocabulary. */}
      <div className="operator-setting-header">
        <span className="operator-setting-label">{t('settings.sectionAuto')}</span>
        <span className={`operator-setting-value ${autoMode === 'off' ? '' : 'is-on'}`}>
          {t(active.labelKey)}
        </span>
      </div>
      <div
        className="segmented"
        role="group"
        aria-label={t('auto.mode')}
        aria-describedby={descriptionId}
      >
        {AUTO_MODE_OPTIONS.map(option => (
          <SegmentedOption
            key={option.value}
            aria-pressed={autoMode === option.value}
            disabled={disabled}
            onClick={() => onAutoModeChange(option.value)}
          >
            {t(option.labelKey)}
          </SegmentedOption>
        ))}
      </div>
      <p className="operator-setting-hint" id={descriptionId}>
        {t(active.descriptionKey)}
      </p>
    </div>
  )
}
