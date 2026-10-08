import type { ReactNode } from 'react'

export interface PanelHintProps {
  icon?: ReactNode
  title: string
  hint?: string
}

/**
 * One-line placeholder for the preview panels.
 *
 * The shared EmptyState is deliberately roomy, which is right for the queue and
 * the history list but would make the compact LIVE and NEXT panels as tall as
 * their content. This keeps those two rows short so the queue stays usable.
 */
export function PanelHint({ icon, title, hint }: PanelHintProps) {
  return (
    <div className="operator-hint">
      {icon && (
        <span className="operator-hint-icon" aria-hidden="true">
          {icon}
        </span>
      )}
      <div className="operator-hint-body">
        <p className="operator-hint-title">{title}</p>
        {hint && <p className="operator-hint-text">{hint}</p>}
      </div>
    </div>
  )
}
