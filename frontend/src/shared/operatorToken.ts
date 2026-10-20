/**
 * localStorage key for the operator token. Saved as soon as the operator types one or the
 * launcher hands one over, and removed again if the server refuses it (close code 1008).
 */
export const OPERATOR_TOKEN_STORAGE_KEY = 'biblelive_operator_token'

/** The stored operator token, or null. Storage can be blocked (private browsing). */
export function readStoredOperatorToken(): string | null {
  try {
    return window.localStorage.getItem(OPERATOR_TOKEN_STORAGE_KEY)
  } catch {
    return null
  }
}

/**
 * Take the token the launcher hands over in the URL fragment (`/operator#token=...`).
 *
 * Fragments are never sent to the server. The fragment is replaced out of the current
 * history entry here, so the address bar and this page's entry no longer show it. The
 * browser's own saved history may still record the address the launcher opened, token
 * included. Returns the trimmed token, or null when the fragment carries none.
 */
export function takeOperatorTokenFragment(): string | null {
  const params = new URLSearchParams(window.location.hash.slice(1))
  if (!params.has('token')) return null
  const token = params.get('token')
  window.history.replaceState(null, '', window.location.pathname + window.location.search)
  return token?.trim() || null
}
