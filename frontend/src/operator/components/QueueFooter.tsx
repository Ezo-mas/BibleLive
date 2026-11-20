import { useRef } from 'react'
import { CheckIcon, XIcon } from '../../components'
import { useI18n } from '../../shared/i18nContext'
import type { AutoMode } from '../../shared/types'
import { PanelMenu } from './PanelMenu'
import type { SideTab } from './SidePanel'

interface QueueFooterProps {
  tab: SideTab
  hasPending: boolean
  canClearPending: boolean
  flash: boolean
  onClearPending: () => void
  autoMode: AutoMode
  disabled: boolean
  onAutoModeChange: (next: AutoMode) => void
}

export function QueueFooter({
  tab,
  hasPending,
  canClearPending,
  flash,
  onClearPending,
  autoMode,
  disabled,
  onAutoModeChange,
}: QueueFooterProps) {
  const { t } = useI18n()
  // Turning it back on restores the last mode used during this console session.
  const lastOnMode = useRef<Exclude<AutoMode, 'off'>>('high')
  if (autoMode !== 'off') lastOnMode.current = autoMode
  const on = autoMode !== 'off'
  // History has no options of its own yet ("Clear history" is in the backlog), so it gets no menu.
  if (tab !== 'pending') return null
  const items = [
    {
      label: t('quick.autoLabel'),
      icon: <CheckIcon />,
      checked: on,
      status: t(on ? 'quick.autoOn' : 'quick.autoOff'),
      disabled,
      onSelect: () => onAutoModeChange(on ? 'off' : lastOnMode.current),
    },
  ]
  const clearQueue = {
    label: t('queue.clear'),
    icon: <XIcon />,
    disabled: !canClearPending,
    destructive: true,
    onSelect: onClearPending,
  }

  return (
    <div className="operator-queue-footer">
      <PanelMenu
        label={t('queue.options')}
        placement="above"
        flash={flash}
        items={[...items, clearQueue]}
      />
      {hasPending && <span className="operator-queue-hint">{t('queue.moveHint')}</span>}
    </div>
  )
}
