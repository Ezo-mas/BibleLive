import type { ReactNode } from 'react'
import { BookIcon, Button, SettingsIcon } from '../../components'
import { useI18n } from '../../shared/i18nContext'
import type { ConnectionState } from '../state/operatorHookTypes'

export interface OperatorHeaderProps {
  connectionState: ConnectionState
  /** Only offer logout when a token is actually in use. */
  showLogout: boolean
  onLogout: () => void
  /** Open the settings dialog (display, theme, and auto-approve). */
  onOpenSettings: () => void
  /** The Find Scripture command bar, rendered in the middle of the bar. */
  search?: ReactNode
  /** The spoken-language readout beside Settings. */
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

const CONNECTION_KEYS = {
  connected: 'connection.connected',
  connecting: 'connection.connecting',
  disconnected: 'connection.disconnected',
} as const

/**
 * Top bar: identity, the Find Scripture command bar, the spoken language, settings,
 * logout.
 *
 * A small warning chip says "Connecting…" while the operator socket connects.
 * A closed socket has its own banner below the bar. Screen readers always get
 * the operator connection state.
 */
export function OperatorHeader({
  connectionState,
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
          <span className="sr-only">
            {t('connection.statusSr', { state: t(CONNECTION_KEYS[connectionState]) })}
          </span>
          {connectionState === 'connecting' && (
            <span className="operator-status-chip" data-testid="status-chip-connecting">
              {t('header.connecting')}
            </span>
          )}
          {language}
        </div>
        <Button
          variant="ghost"
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
