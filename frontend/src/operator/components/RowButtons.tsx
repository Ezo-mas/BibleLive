import { Button, TrashIcon } from '../../components'
import { useI18n } from '../../shared/i18nContext'

export interface RowShowButtonProps {
  /** The row's title, so the button is described by the row it is on. */
  titleId: string
  /** The row that holds the keyboard cursor. */
  highlighted?: boolean
  disabled?: boolean
  onClick: () => void
}

/** Show: the primary action of a row in Pending, Plan and History, always its right-most control. */
export function RowShowButton({
  titleId,
  highlighted = false,
  disabled = false,
  onClick,
}: RowShowButtonProps) {
  const { t } = useI18n()
  return (
    <Button
      variant={highlighted ? 'primary' : 'secondary'}
      size="sm"
      aria-describedby={titleId}
      disabled={disabled}
      onClick={event => {
        // The second click of a double-click is not a second Show: once this row is shown
        // (or leaves Pending), the next row's Show can sit under the pointer.
        if (event.detail > 1) return
        onClick()
      }}
    >
      {t('row.show')}
    </Button>
  )
}

export interface RowRemoveButtonProps {
  /** The full accessible name, naming the reference ("Remove John 3:16 from the plan"). */
  label: string
  disabled?: boolean
  onClick: () => void
}

/**
 * Remove: an icon-only square, named by its label and tooltip.
 * It sits a gap away from Show, never touching it, so the two are hard to confuse.
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
        // As with Show: a double-click removes one row, not the one that slides up next.
        if (event.detail > 1) return
        onClick()
      }}
    />
  )
}
