import { ShieldIcon } from '../../components'
import { useI18n } from '../../shared/i18nContext'
import { autoModeOption } from '../autoMode'
import type { AutoMode } from '../../shared/types'

export interface AutoApproveCompactProps {
  autoMode: AutoMode
  /** Open the settings dialog on the auto-approve section. */
  onOpenSettings: () => void
}

/**
 * The one piece of auto-approve on the main screen: its current mode.
 *
 * Auto-approve decides what reaches the projectors by itself, so the operator
 * has to be able to see at a glance that it is not Off — but it is a setting,
 * not a moment-to-moment action, and it must never outrank On Display, Up Next,
 * or Approve and Show. So the console shows the mode as a compact chip and
 * keeps every control that changes it in Settings, one click away.
 *
 * The label comes from the same option table the settings buttons use, so the
 * chip cannot disagree with the dialog, and the mode is tinted when it is
 * actively approving so "Off" is not the only readable difference.
 */
export function AutoApproveCompact({ autoMode, onOpenSettings }: AutoApproveCompactProps) {
  const { t } = useI18n()
  const active = autoModeOption(autoMode)

  return (
    <button
      type="button"
      className={`operator-auto-compact ${autoMode === 'off' ? '' : 'is-on'}`}
      onClick={onOpenSettings}
      aria-haspopup="dialog"
      title={t('auto.compactTitle')}
    >
      <span className="operator-auto-compact-icon" aria-hidden="true">
        <ShieldIcon />
      </span>
      <span className="operator-auto-compact-text">
        {t('auto.compactLabel')}
        <strong className="operator-auto-compact-value">{t(active.labelKey)}</strong>
      </span>
    </button>
  )
}
