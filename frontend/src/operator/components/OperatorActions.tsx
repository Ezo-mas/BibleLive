import { Button, XIcon } from '../../components'
import type { FlashTarget } from '../hooks/useOperatorState'

export interface ClearDisplayButtonProps {
  canClearDisplay: boolean
  flash: FlashTarget | null
  onClearDisplay: () => void
}

/**
 * Clear Display: take the projector back to its waiting screen.
 *
 * It lives under ON DISPLAY, next to what it clears, and only does anything while
 * the display has content.
 */
export function ClearDisplayButton({
  canClearDisplay,
  flash,
  onClearDisplay,
}: ClearDisplayButtonProps) {
  return (
    <Button
      variant="danger"
      size="md"
      icon={<XIcon />}
      onClick={onClearDisplay}
      disabled={!canClearDisplay}
      className={flash === 'clear' ? 'btn-flash' : ''}
    >
      Clear Display
    </Button>
  )
}

export interface QueueFooterProps {
  canClearPending: boolean
  flash: FlashTarget | null
  onClearPending: () => void
}

/**
 * The foot of the pending queue: Clear Queue, and a reminder of the keys that
 * move through it.
 *
 * There is no approve button here. Showing a queued detection is Show on display
 * under UP NEXT — the same request the old Approve button sent — so the console
 * has one primary action instead of two that did the same thing.
 */
export function QueueFooter({ canClearPending, flash, onClearPending }: QueueFooterProps) {
  return (
    <div className="operator-queue-footer">
      <Button
        variant="ghost"
        size="sm"
        onClick={onClearPending}
        disabled={!canClearPending}
        className={flash === 'clearPending' ? 'btn-flash' : ''}
      >
        Clear Queue
      </Button>
      <span className="operator-queue-hint">↑ ↓ to move</span>
    </div>
  )
}
