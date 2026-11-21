import { Button, TrashIcon } from '../../components'
export interface RowRemoveButtonProps {
  /** The full accessible name, naming the reference ("Remove John 3:16 from the plan"). */
  label: string
  disabled?: boolean
  onClick: () => void
}

/**
 * Remove: an icon-only square, named by its label and tooltip.
 */
export function RowRemoveButton({ label, disabled = false, onClick }: RowRemoveButtonProps) {
  return (
    <Button
      variant="ghost"
      size="sm"
      className="operator-row-remove"
      iconOnly
      icon={<TrashIcon />}
      aria-label={label}
      title={label}
      disabled={disabled}
      onClick={event => {
        // A double-click removes one row, even if the next row slides up.
        if (event.detail > 1) return
        onClick()
      }}
    />
  )
}
