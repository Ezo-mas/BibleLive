import { readFailure } from './passageApi'

/**
 * The languages the audio pipeline has speech models and reference parsers for.
 *
 * Mirrors `WHISPER_INITIAL_PROMPTS` in `backend/audio/stt.py`. The pipeline
 * validates every request itself, so a language missing from this list costs a
 * button, never a wrong answer — and one that is here but not supported comes
 * back as the pipeline's own refusal.
 */
export const SPOKEN_LANGUAGES = [
  { code: 'en', label: 'English' },
  { code: 'lt', label: 'Lithuanian' },
] as const

/** A language's name for the operator; an unknown code is shown as itself. */
export function spokenLanguageLabel(code: string): string {
  return SPOKEN_LANGUAGES.find(language => language.code === code)?.label ?? code.toUpperCase()
}

/**
 * A language request that failed, carrying the server's own explanation.
 *
 * `status` is 0 for a request that never reached the server. 503 means the
 * server is fine but the audio pipeline is not running (or has its control API
 * switched off); the operator needs those told apart, because "start the
 * pipeline" and "check your connection" are different fixes.
 */
export class LanguageApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'LanguageApiError'
    this.status = status
  }
}

/** The language endpoint is operator-only, so requests carry the operator token. */
function headers(token: string | null, json = false): HeadersInit {
  return {
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...(json ? { 'Content-Type': 'application/json' } : {}),
  }
}

async function readLanguage(response: Response): Promise<string> {
  if (!response.ok) {
    throw new LanguageApiError(
      response.status,
      await readFailure(response, `Language control failed (HTTP ${response.status}).`)
    )
  }
  const body = (await response.json()) as { language?: unknown } | null
  if (typeof body?.language !== 'string' || body.language === '') {
    throw new LanguageApiError(502, 'The server sent a language reply the console could not read.')
  }
  return body.language
}

/** The language the audio pipeline is transcribing in right now. */
export async function fetchSpokenLanguage(token: string | null): Promise<string> {
  let response: Response
  try {
    response = await fetch('/api/language', { headers: headers(token) })
  } catch {
    throw new LanguageApiError(0, 'Could not reach the server to read the spoken language.')
  }
  return readLanguage(response)
}

/**
 * Switch the audio pipeline's language, without restarting it.
 *
 * Resolves with the language the pipeline confirms, which is what the console
 * shows — never the value it asked for.
 */
export async function changeSpokenLanguage(
  language: string,
  token: string | null
): Promise<string> {
  let response: Response
  try {
    response = await fetch('/api/language', {
      method: 'POST',
      headers: headers(token, true),
      body: JSON.stringify({ language }),
    })
  } catch {
    throw new LanguageApiError(0, 'Could not reach the server to change the spoken language.')
  }
  return readLanguage(response)
}
