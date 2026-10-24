import { ApiError, authHeaders, request } from './apiClient'
import { defaultTranslator } from '../shared/i18n'
import type { MessageKey, Translator } from '../shared/i18n'
import type { IdleScreenFit } from '../shared/idleScreen'

/** The largest picture the server accepts. The console checks it too, to fail before uploading. */
export const IDLE_SCREEN_MAX_BYTES = 8 * 1024 * 1024

/** The idle screen as the server reports it: the fit mode, and the picture's version when one is set. */
export interface IdleScreenInfo {
  fit: IdleScreenFit
  image: { version: string } | null
}

/** Validate the shape of a reply from any idle-screen endpoint. */
export function parseIdleScreenInfo(body: unknown): IdleScreenInfo | null {
  if (typeof body !== 'object' || body === null) return null
  const { fit, image } = body as { fit?: unknown; image?: unknown }
  if (fit !== 'logo' && fit !== 'fill') return null
  if (image === null) return { fit, image: null }
  if (typeof image !== 'object' || image === null) return null
  const { version } = image as { version?: unknown }
  if (typeof version !== 'string') return null
  return { fit, image: { version } }
}

/** The message for a failed request. The server's own English text is not shown. */
function failureText(status: number, failedKey: MessageKey, t: Translator): string {
  if (status === 401) return t('api.unauthorized')
  if (status === 413) {
    return t('idle.tooLarge', { megabytes: IDLE_SCREEN_MAX_BYTES / (1024 * 1024) })
  }
  if (status === 415) return t('idle.notSupported')
  return t(failedKey, { status })
}

interface RequestMessages {
  /** Shown when the request never reached the server. */
  unreachable: MessageKey
  /** Shown with the HTTP status when the server refused or failed. */
  failed: MessageKey
}

async function send(
  path: string,
  init: RequestInit,
  messages: RequestMessages,
  t: Translator
): Promise<IdleScreenInfo> {
  const response = await request(path, init, t(messages.unreachable))
  if (!response.ok) {
    throw new ApiError(response.status, failureText(response.status, messages.failed, t))
  }
  let body: unknown = null
  try {
    body = await response.json()
  } catch {
    // Not JSON: handled as a reply the console cannot read, below.
  }
  const info = parseIdleScreenInfo(body)
  if (!info) throw new ApiError(502, t('idle.badReply'))
  return info
}

/** The idle screen's fit mode and whether a picture is set. */
export function fetchIdleScreen(
  token: string | null,
  t: Translator = defaultTranslator
): Promise<IdleScreenInfo> {
  return send(
    '/api/idle-screen',
    { headers: authHeaders(token) },
    { unreachable: 'idle.readUnreachable', failed: 'idle.readFailed' },
    t
  )
}

/**
 * Store `file` as the idle picture. The body is the file itself; the server finds its format
 * from its first bytes and ignores the content type and name the browser sends.
 */
export function uploadIdleScreen(
  file: Blob,
  token: string | null,
  t: Translator = defaultTranslator
): Promise<IdleScreenInfo> {
  return send(
    '/api/idle-screen',
    { method: 'POST', headers: authHeaders(token), body: file },
    { unreachable: 'idle.unreachable', failed: 'idle.uploadFailed' },
    t
  )
}

/** Choose how the picture fills the screen: `logo` or `fill`. */
export function changeIdleScreenFit(
  fit: IdleScreenFit,
  token: string | null,
  t: Translator = defaultTranslator
): Promise<IdleScreenInfo> {
  return send(
    '/api/idle-screen/fit',
    { method: 'PUT', headers: authHeaders(token, true), body: JSON.stringify({ fit }) },
    { unreachable: 'idle.unreachable', failed: 'idle.fitFailed' },
    t
  )
}

/** Remove the picture; the display goes back to blank while idle. */
export function removeIdleScreen(
  token: string | null,
  t: Translator = defaultTranslator
): Promise<IdleScreenInfo> {
  return send(
    '/api/idle-screen',
    { method: 'DELETE', headers: authHeaders(token) },
    { unreachable: 'idle.unreachable', failed: 'idle.removeFailed' },
    t
  )
}
