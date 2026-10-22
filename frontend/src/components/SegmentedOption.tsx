import type { ButtonHTMLAttributes, ReactNode } from 'react'

export interface SegmentedOptionProps extends Omit<
  ButtonHTMLAttributes<HTMLButtonElement>,
  'children' | 'type'
> {
  /** An icon drawn before the label. */
  icon?: ReactNode
  /** The label, as plain text: it is also reserved in bold so selecting never changes the width. */
  children: string | number
}

/**
 * One option of a `.segmented` control (styles/controls.css).
 *
 * The selected option is drawn bold as a cue that does not rely on colour. Bold text
 * is wider, so the label also carries a hidden bold copy of itself (`data-label`):
 * the option is always as wide as its bold label, and the track keeps the same width
 * whichever option is selected.
 */
export function SegmentedOption({ icon, children, ...props }: SegmentedOptionProps) {
  return (
    <button type="button" className="segmented-option" {...props}>
      {icon}
      <span className="segmented-label" data-label={String(children)}>
        {children}
      </span>
    </button>
  )
}
