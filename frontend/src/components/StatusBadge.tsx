import { ReactNode } from 'react'
import './StatusBadge.css'

export type BadgeVariant = 'success' | 'warning' | 'danger' | 'info' | 'neutral'

export interface StatusBadgeProps {
  variant: BadgeVariant
  children: ReactNode
  className?: string
}

export function StatusBadge({ variant, children, className = '' }: StatusBadgeProps) {
  const classes = ['status-badge', `status-badge-${variant}`, className].filter(Boolean).join(' ')

  return <span className={classes}>{children}</span>
}
