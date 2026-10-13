import type { ReactNode } from 'react'
import { ConnectionIndicator, SettingsIcon } from '../../components'
import type { ConnectionState } from '../../components'

export interface OperatorHeaderProps {
  connectionState: ConnectionState
  /** True when at least one display page is connected. */
  displayConnected: boolean
  /** Only offer logout when a token is actually in use. */
  showLogout: boolean
  onLogout: () => void
  /** Open the settings dialog (display, language, theme, and auto-approve). */
  onOpenSettings: () => void
  /** The spoken-language reading, shown beside the connection health. */
  language?: ReactNode
  /**
   * Action feedback, rendered between the title and the controls.
   *
   * The notice belongs here rather than floating over the console: an overlay
   * sat on the action row on a laptop and on the first queue card on a short
   * landscape window, and those are the controls the operator reaches for next.
   */
  feedback?: ReactNode
}

/**
 * Top bar: identity, connection health, display status, settings, logout.
 *
 * One slim row. The theme buttons this used to carry now live in the settings
 * dialog with every other preference, so the bar holds only what the operator
 * needs while the service is running: that the console is connected, that the
 * projector is live, and one way into the settings.
 */
export function OperatorHeader({
  connectionState,
  displayConnected,
  showLogout,
  onLogout,
  onOpenSettings,
  language,
  feedback,
}: OperatorHeaderProps) {
  return (
    <header className="operator-header">
      <div className="operator-header-left">
        <h1 className="operator-title">BibleLive</h1>
        <span className="operator-subtitle">Operator Console</span>
      </div>

      {feedback}

      <div className="operator-header-right">
        <div className="operator-status-group">
          {language}
          <ConnectionIndicator state={connectionState} />
          {displayConnected && (
            <div className="operator-display-status">
              <span className="operator-display-dot" />
              <span className="operator-display-label">Display Active</span>
            </div>
          )}
        </div>
        <button
          type="button"
          className="operator-icon-button"
          onClick={onOpenSettings}
          aria-label="Settings"
          aria-haspopup="dialog"
          title="Settings"
        >
          <SettingsIcon />
        </button>
        {showLogout && (
          <button onClick={onLogout} className="operator-logout-button" title="Logout">
            Logout
          </button>
        )}
      </div>
    </header>
  )
}
