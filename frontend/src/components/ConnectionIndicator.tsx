import './ConnectionIndicator.css'

export type ConnectionState = 'connected' | 'connecting' | 'disconnected'

export interface ConnectionIndicatorProps {
  state: ConnectionState
  compact?: boolean
}

const stateLabels: Record<ConnectionState, string> = {
  connected: 'Connected',
  connecting: 'Connecting',
  disconnected: 'Disconnected',
}

export function ConnectionIndicator({ state, compact = false }: ConnectionIndicatorProps) {
  return (
    <div className={`connection-indicator connection-indicator-${state}`}>
      <span className={`connection-dot connection-dot-${state}`} aria-hidden="true" />
      {!compact && <span className="connection-label">{stateLabels[state]}</span>}
      <span className="sr-only">Connection status: {stateLabels[state]}</span>
    </div>
  )
}
