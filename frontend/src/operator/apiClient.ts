import { defaultTranslator } from '../shared/i18n'
import type { Translator } from '../shared/i18n'

/**
 * A console request that failed, with the status it failed with and one line for the operator.
 *
 * `status` is 0 for a request that never reached the server (offline, aborted by
 * the browser), which is the one case the operator needs to be told apart from a
 * rejection: "check the reference" is wrong advice when the network is down. 502 is
 * the console's own "the server answered, but not with what it should have".
 */
export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

/**
 * Headers for an operator request. The token goes in the bearer header when there is
 * one; a null or empty token sends no Authorization header at all. `json` adds the
 * content type for a JSON body.
 */
export function authHeaders(token: string | null, json = false): HeadersInit {
  return {
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...(json ? { 'Content-Type': 'application/json' } : {}),
  }
}

/**
 * Send one request. Only a request that gets no answer throws, as an ApiError with
 * status 0 and `unreachableMessage`; any answer, a refusal included, is returned for
 * the caller to check with `response.ok`.
 */
export async function request(
  path: string,
  init: RequestInit,
  unreachableMessage: string
): Promise<Response> {
  try {
    return await fetch(path, init)
  } catch {
    throw new ApiError(0, unreachableMessage)
  }
}

/**
 * Turn an unsuccessful response into one line for the operator.
 *
 * FastAPI sends a string detail for our own rejections (the readable messages
 * from the passage resolver) and a list of issues for parameter validation;
 * both are worth showing as-is rather than replacing with a generic failure.
 */
export async function readFailure(
  response: Response,
  fallback?: string,
  t: Translator = defaultTranslator
): Promise<string> {
  try {
    const body: unknown = await response.json()
    const detail = (body as { detail?: unknown } | null)?.detail
    if (typeof detail === 'string' && detail.trim()) return detail
    if (Array.isArray(detail)) {
      const messages = detail
        .map(issue => (issue as { msg?: unknown })?.msg)
        .filter((msg): msg is string => typeof msg === 'string' && msg.trim().length > 0)
      if (messages.length) return messages.join('; ')
    }
  } catch {
    // Not JSON (a proxy error page, an empty body): fall through to the status.
  }
  if (response.status === 401) return t('api.unauthorized')
  return fallback ?? t('api.lookupFailed', { status: response.status })
}
