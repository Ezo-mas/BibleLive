import type { FormEvent } from 'react'

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
  return (
    <div className="operator-app">
      <div className="operator-auth-container">
        <div className="operator-auth-card">
          <h1 className="operator-auth-title">BibleLive Operator</h1>
          {authFailed && (
            // Announced when a rejected token brings the operator back here.
            <div className="operator-auth-error" role="alert">
              Authentication failed. Please enter your operator token.
            </div>
          )}
          <form onSubmit={onSubmit} className="operator-auth-form">
            <label htmlFor="token-input" className="operator-auth-label">
              Operator Token
            </label>
            <input
              id="token-input"
              type="password"
              value={tokenInput}
              onChange={e => onTokenInputChange(e.target.value)}
              placeholder="Enter your operator token"
              className="operator-auth-input"
              autoFocus
            />
            <button type="submit" className="operator-auth-button">
              Connect
            </button>
          </form>
        </div>
      </div>
    </div>
  )
}
