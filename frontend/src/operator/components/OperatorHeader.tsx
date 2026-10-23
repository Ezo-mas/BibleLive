import type { ReactNode } from 'react'
import { BookIcon, Button, ConnectionIndicator, SettingsIcon } from '../../components'
import type { ConnectionState } from '../../components'
import { useI18n } from '../../shared/i18nContext'

export interface OperatorHeaderProps {
  connectionState: ConnectionState
  /** True when at least one display page is connected. */
  displayConnected: boolean
  /** Only offer logout when a token is actually in use. */
  showLogout: boolean
  onLogout: () => void
  /** Open the settings dialog (display, theme, and auto-approve). */
  onOpenSettings: () => void
  /** The Find Scripture command bar, rendered in the middle of the bar. */
  search?: ReactNode
  /** The spoken-language reading, shown beside the connection health. */
  language?: ReactNode
  /**
   * Action feedback, which hangs from the bottom edge of the bar.
   *
   * The notice belongs here rather than floating over the console: an overlay
   * sat on the action row on a laptop and on the first queue card on a short
   * landscape window, and those are the controls the operator reaches for next.
   */
  feedback?: ReactNode
}

/**
 * Top bar: identity, the Find Scripture command bar, connection health, display
 * status, settings, logout.
 *
 * One slim row. The theme buttons this used to carry live in the settings dialog
 * with every other preference, so the bar holds only what the operator needs
 * while the service is running: a way to find a passage, that the console is
 * connected, that the projector is live, and one way into the settings.
 */
export function OperatorHeader({
  connectionState,
  displayConnected,
  showLogout,
  onLogout,
  onOpenSettings,
  search,
  language,
  feedback,
}: OperatorHeaderProps) {
  const { t } = useI18n()
  return (
    <header className="operator-header">
      <div className="operator-header-left">
        <span className="operator-brand-mark" aria-hidden="true">
          <BookIcon />
        </span>
        <h1 className="operator-title">BibleLive</h1>
      </div>

      {search}

      {feedback}

      <div className="operator-header-right">
        <div className="operator-status-group">
          {language}
          <ConnectionIndicator state={connectionState} />
          {displayConnected && (
            <div className="operator-display-status">
              <span className="operator-display-dot" />
              <span className="operator-display-label">{t('header.displayActive')}</span>
            </div>
          )}
        </div>
        <Button
          variant="secondary"
          iconOnly
          icon={<SettingsIcon />}
          onClick={onOpenSettings}
          aria-label={t('header.settings')}
          aria-haspopup="dialog"
          title={t('header.settings')}
        />
        {showLogout && (
          <Button variant="secondary" onClick={onLogout} title={t('header.logout')}>
            {t('header.logout')}
          </Button>
        )}
      </div>
    </header>
  )
}
