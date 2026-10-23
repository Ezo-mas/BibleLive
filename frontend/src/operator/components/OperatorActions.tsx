import { Button } from '../../components'
import { useI18n } from '../../shared/i18nContext'
import type { FlashTarget } from '../hooks/useOperatorState'

export interface QueueFooterProps {
  canClearPending: boolean
  flash: FlashTarget | null
  onClearPending: () => void
}

/**
 * The foot of the pending queue: Clear Queue, and a reminder of the keys that
 * move through it.
 *
 * There is no approve button here. Showing a queued detection is the Show button
 * on its own row, or Show on display under UP NEXT once it is staged — the same
 * request the old Approve button sent.
 */
export function QueueFooter({ canClearPending, flash, onClearPending }: QueueFooterProps) {
  const { t } = useI18n()
  return (
    <div className="operator-queue-footer">
      <Button
        variant="secondary"
        size="sm"
        onClick={onClearPending}
        disabled={!canClearPending}
        className={flash === 'clearPending' ? 'btn-flash' : ''}
      >
        {t('queue.clear')}
      </Button>
      <span className="operator-queue-hint">{t('queue.moveHint')}</span>
    </div>
  )
}
