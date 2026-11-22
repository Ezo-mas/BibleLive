import { Button, TrashIcon } from '../../components'
import { useI18n } from '../../shared/i18nContext'

interface RowShowButtonProps {
  referenceLabel: string
  selected: boolean
  disabled: boolean
  onClick: () => void
}

/** The Show button reserves its space on touch, even before the row is selected. */
export function RowShowButton({ referenceLabel, selected, disabled, onClick }: RowShowButtonProps) {
  const { t } = useI18n()
  return (
    <Button
      variant="primary"
      size="sm"
      className="operator-row-show"
      aria-label={t('follow.showAria', { label: referenceLabel })}
      aria-hidden={!selected || undefined}
      tabIndex={selected ? undefined : -1}
      disabled={disabled}
      onClick={event => {
        if (event.detail <= 1) onClick()
      }}
    >
      {t('follow.show')}
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
