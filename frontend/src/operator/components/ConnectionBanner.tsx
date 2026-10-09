import { AlertCircleIcon, Button } from '../../components'

export interface ConnectionBannerProps {
  onReconnect: () => void
}

/**
 * Shown while the console has no server connection.
 *
 * A closed socket is not cosmetic: every control on the page is disabled and
 * cannot reach the server, and the automatic retries are finite. Rather than
 * leaving the operator to guess why nothing responds, the banner says what is
 * true and offers a manual reconnect through the shared hook. It renders below
 * the header and never replaces the console, so the last known queue and display
 * stay readable.
 */
export function ConnectionBanner({ onReconnect }: ConnectionBannerProps) {
  return (
    <div className="operator-connection-banner" role="alert">
      <span className="operator-connection-banner-icon" aria-hidden="true">
        <AlertCircleIcon />
      </span>
      <p className="operator-connection-banner-text">
        <strong>Not connected to the server.</strong> Approvals and settings changes are paused
        until the connection is back.
      </p>
      <Button variant="secondary" size="sm" onClick={onReconnect}>
        Reconnect
      </Button>
    </div>
  )
}
