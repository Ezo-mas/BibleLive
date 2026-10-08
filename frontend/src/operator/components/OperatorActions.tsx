import { Button, CheckIcon, XIcon } from '../../components'
import type { FlashTarget } from '../hooks/useOperatorState'

export interface OperatorActionsProps {
  canApprove: boolean
  canClearDisplay: boolean
  canClearPending: boolean
  flash: FlashTarget | null
  onApprove: () => void
  onClearDisplay: () => void
  onClearPending: () => void
}

/**
 * The three operator actions. "Approve and Show" is the only path from the
 * pending queue to the display, and it is disabled unless an item is selected.
 */
export function OperatorActions({
  canApprove,
  canClearDisplay,
  canClearPending,
  flash,
  onApprove,
  onClearDisplay,
  onClearPending,
}: OperatorActionsProps) {
  return (
    <div className="operator-actions">
      <Button
        variant="primary"
        size="lg"
        fullWidth
        icon={<CheckIcon />}
        onClick={onApprove}
        disabled={!canApprove}
        className={flash === 'approve' ? 'btn-flash' : ''}
      >
        Approve and Show
      </Button>

      <div className="operator-actions-secondary">
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

        <Button
          variant="secondary"
          size="md"
          icon={<XIcon />}
          onClick={onClearPending}
          disabled={!canClearPending}
          className={flash === 'clearPending' ? 'btn-flash' : ''}
        >
          Clear Queue
        </Button>
      </div>
    </div>
  )
}
