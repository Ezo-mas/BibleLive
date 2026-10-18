import { readFailure } from './passageApi'
import { defaultTranslator } from '../shared/i18n'
import type { Translator } from '../shared/i18n'

/**
 * The languages the audio pipeline has speech models and reference parsers for.
 *
 * Mirrors `WHISPER_INITIAL_PROMPTS` in `backend/audio/stt.py`. The pipeline
 * validates every request itself, so a language missing from this list costs a
 * button, never a wrong answer — and one that is here but not supported comes
 * back as the pipeline's own refusal.
 */
export const SPOKEN_LANGUAGES = [
  { code: 'en', labelKey: 'language.en' },
  { code: 'lt', labelKey: 'language.lt' },
] as const

/** A language's name for the operator; an unknown code is shown as itself. */
export function spokenLanguageLabel(code: string, t: Translator = defaultTranslator): string {
  const language = SPOKEN_LANGUAGES.find(option => option.code === code)
  return language ? t(language.labelKey) : code.toUpperCase()
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

async function readLanguage(response: Response, t: Translator): Promise<string> {
  if (!response.ok) {
    throw new LanguageApiError(
      response.status,
      await readFailure(response, t('language.controlFailed', { status: response.status }), t)
    )
  }
  const body = (await response.json()) as { language?: unknown } | null
  if (typeof body?.language !== 'string' || body.language === '') {
    throw new LanguageApiError(502, t('language.badReply'))
  }
  return body.language
}

/** The language the audio pipeline is transcribing in right now. */
export async function fetchSpokenLanguage(
  token: string | null,
  t: Translator = defaultTranslator
): Promise<string> {
  let response: Response
  try {
    response = await fetch('/api/language', { headers: headers(token) })
  } catch {
    throw new LanguageApiError(0, t('language.readUnreachable'))
  }
  return readLanguage(response, t)
}

/**
 * Switch the audio pipeline's language, without restarting it.
 *
 * Resolves with the language the pipeline confirms, which is what the console
 * shows — never the value it asked for.
 */
export async function changeSpokenLanguage(
  language: string,
  token: string | null,
  t: Translator = defaultTranslator
): Promise<string> {
  let response: Response
  try {
    response = await fetch('/api/language', {
      method: 'POST',
      headers: headers(token, true),
      body: JSON.stringify({ language }),
    })
  } catch {
    throw new LanguageApiError(0, t('language.changeUnreachable'))
  }
  return readLanguage(response, t)
}
