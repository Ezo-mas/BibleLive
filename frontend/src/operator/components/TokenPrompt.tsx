import type { FormEvent } from 'react'
import { useI18n } from '../../shared/i18nContext'

export interface TokenPromptProps {
  /** True when the server rejected the previous token (WebSocket close 1008). */
  authFailed: boolean
  tokenInput: string
  onTokenInputChange: (value: string) => void
  onSubmit: (event: FormEvent) => void
}

/**
 * Gate shown until the operator supplies a token — either because none is
 * stored yet or because the server rejected the stored one.
 */
export function TokenPrompt({
  authFailed,
  tokenInput,
  onTokenInputChange,
  onSubmit,
}: TokenPromptProps) {
  const { t } = useI18n()
  return (
    <div className="operator-app">
      <div className="operator-auth-container">
        <div className="operator-auth-card">
          <h1 className="operator-auth-title">{t('app.operatorTitle')}</h1>
          {authFailed && (
            // Announced when a rejected token brings the operator back here.
            <div className="operator-auth-error" role="alert">
              {t('token.authFailed')}
            </div>
          )}
          <form onSubmit={onSubmit} className="operator-auth-form">
            <label htmlFor="token-input" className="operator-auth-label">
              {t('token.label')}
            </label>
            <input
              id="token-input"
              type="password"
              value={tokenInput}
              onChange={e => onTokenInputChange(e.target.value)}
              placeholder={t('token.placeholder')}
              className="operator-auth-input"
              autoFocus
            />
            <button type="submit" className="operator-auth-button">
              {t('token.connect')}
            </button>
          </form>
        </div>
      </div>
    </div>
  )
}
