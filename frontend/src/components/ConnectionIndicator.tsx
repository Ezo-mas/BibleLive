import { useI18n } from '../shared/i18nContext'
import './ConnectionIndicator.css'

export type ConnectionState = 'connected' | 'connecting' | 'disconnected'

export interface ConnectionIndicatorProps {
  state: ConnectionState
  compact?: boolean
}

const stateKeys = {
  connected: 'connection.connected',
  connecting: 'connection.connecting',
  disconnected: 'connection.disconnected',
} as const

export function ConnectionIndicator({ state, compact = false }: ConnectionIndicatorProps) {
  const { t } = useI18n()
  const label = t(stateKeys[state])
  return (
    <div className={`connection-indicator connection-indicator-${state}`}>
      <span className={`connection-dot connection-dot-${state}`} aria-hidden="true" />
      {!compact && <span className="connection-label">{label}</span>}
      <span className="sr-only">{t('connection.statusSr', { state: label })}</span>
    </div>
  )
}
