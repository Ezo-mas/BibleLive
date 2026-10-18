/** localStorage key for the operator token, saved once the server accepts it. */
export const OPERATOR_TOKEN_STORAGE_KEY = 'biblelive_operator_token'

/** The stored operator token, or null. Storage can be blocked (private browsing). */
export function readStoredOperatorToken(): string | null {
  try {
    return window.localStorage.getItem(OPERATOR_TOKEN_STORAGE_KEY)
  } catch {
    return null
  }
}
