import { readFailure } from './passageApi'

/** The active Bible translation and the ones the server can switch to. */
export interface BibleVersionInfo {
  version: string | null
  available: string[]
}

/** Names for the translations the project ships; any other id is shown in capitals. */
const VERSION_LABELS: Record<string, string> = {
  kjv: 'King James (KJV)',
  web: 'World English (WEB)',
  ltb: 'Lithuanian (LTB)',
}

/** A translation's name for the operator; an unknown id is shown as itself. */
export function bibleVersionLabel(id: string): string {
  return VERSION_LABELS[id] ?? id.toUpperCase()
}

/** A Bible-version request that failed; `status` is 0 when it never reached the server. */
export class BibleVersionApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'BibleVersionApiError'
    this.status = status
  }
}

/** The endpoint is operator-only, so requests carry the operator token. */
function headers(token: string | null, json = false): HeadersInit {
  return {
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...(json ? { 'Content-Type': 'application/json' } : {}),
  }
}

/** Validate the shape of a version reply; shared with the WebSocket confirmation. */
export function parseBibleVersionInfo(body: unknown): BibleVersionInfo | null {
  if (typeof body !== 'object' || body === null) return null
  const { version, available } = body as { version?: unknown; available?: unknown }
  if (version !== null && typeof version !== 'string') return null
  if (!Array.isArray(available) || !available.every(item => typeof item === 'string')) return null
  return { version, available }
}

async function readInfo(response: Response): Promise<BibleVersionInfo> {
  if (!response.ok) {
    throw new BibleVersionApiError(
      response.status,
      await readFailure(response, `Bible version request failed (HTTP ${response.status}).`)
    )
  }
  const info = parseBibleVersionInfo(await response.json())
  if (!info) {
    throw new BibleVersionApiError(
      502,
      'The server sent a Bible version reply the console could not read.'
    )
  }
  return info
}

/** Which translation is active, and which others can be chosen. */
export async function fetchBibleVersion(token: string | null): Promise<BibleVersionInfo> {
  let response: Response
  try {
    response = await fetch('/api/bible-version', { headers: headers(token) })
  } catch {
    throw new BibleVersionApiError(0, 'Could not reach the server to read the Bible version.')
  }
  return readInfo(response)
}

/** Switch the translation all verse text comes from; resolves with what the server confirms. */
export async function changeBibleVersion(
  version: string,
  token: string | null
): Promise<BibleVersionInfo> {
  let response: Response
  try {
    response = await fetch('/api/bible-version', {
      method: 'POST',
      headers: headers(token, true),
      body: JSON.stringify({ version }),
    })
  } catch {
    throw new BibleVersionApiError(0, 'Could not reach the server to change the Bible version.')
  }
  return readInfo(response)
}
