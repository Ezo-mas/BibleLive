import { en } from './messages.en'
import { lt } from './messages.lt'

/**
 * The console's interface languages and the small typed dictionary behind `t`.
 *
 * No library: the English table in `messages.en.ts` defines the keys, and the
 * Lithuanian table is typed to match it, so a missing or extra key is a compile
 * error instead of a blank label on screen.
 */

/** The interface languages. */
export type Language = 'en' | 'lt'

/** Values for `{name}` placeholders in a message. */
export type MessageParams = Record<string, string | number>

/**
 * A message with plural forms. The `count` parameter picks the form for the
 * language (see `pluralCategory`); each form is a whole template with `{count}`.
 */
export interface PluralMessage {
  one: string
  /** Lithuanian only (2–9, 22–29, …). English never selects it. */
  few?: string
  other: string
}

export type MessageEntry = string | PluralMessage

/** Every translatable key. The English table defines the set. */
export type MessageKey = keyof typeof en

/** Translates a key, filling in `{name}` placeholders from `params`. */
export type Translator = (key: MessageKey, params?: MessageParams) => string

const TABLES: Record<Language, Record<MessageKey, MessageEntry>> = { en, lt }

/** localStorage key for this browser's own language choice (absent = follow the launcher). */
export const LANGUAGE_STORAGE_KEY = 'biblelive_ui_language'

export function isLanguage(value: unknown): value is Language {
  return value === 'en' || value === 'lt'
}

/** Which plural form a count takes. */
export type PluralCategory = 'one' | 'few' | 'other'

/**
 * Lithuanian: 1, 21, 31… → one; 2–9, 22–29… → few; 0, 10–20, 30…, 11, 111 → other.
 * English: 1 → one; everything else → other.
 */
export function pluralCategory(language: Language, count: number): PluralCategory {
  const n = Math.abs(count)
  if (language === 'lt') {
    if (n % 10 === 1 && n % 100 !== 11) return 'one'
    if (n % 10 >= 2 && n % 10 <= 9 && (n % 100 < 12 || n % 100 > 19)) return 'few'
    return 'other'
  }
  return n === 1 ? 'one' : 'other'
}

function interpolate(template: string, params: MessageParams = {}): string {
  return template.replace(/\{(\w+)\}/g, (placeholder, name: string) =>
    Object.prototype.hasOwnProperty.call(params, name) ? String(params[name]) : placeholder
  )
}

/** A translator for one language. A plural message uses `params.count` to pick its form. */
export function translatorFor(language: Language): Translator {
  const table = TABLES[language]
  return (key, params) => {
    const entry = table[key]
    if (typeof entry === 'string') return interpolate(entry, params)
    const count = params?.count
    const form = typeof count === 'number' ? pluralCategory(language, count) : 'other'
    return interpolate(entry[form] ?? entry.other, params)
  }
}

/**
 * English, for code that is handed no translator (helpers used outside React, and
 * tests). Components always take theirs from the console's language.
 */
export const defaultTranslator: Translator = translatorFor('en')

/** Whether a string is a translatable key (for keys built at run time). */
export function hasMessage(key: string): key is MessageKey {
  return Object.prototype.hasOwnProperty.call(en, key)
}

export interface LanguageSources {
  /** This browser's own choice, or null to follow the launcher and then the browser. */
  override: Language | null
  /** The launcher's setting from the server, or null when the server did not say. */
  server: Language | null
  /** The browser's language tag, e.g. 'lt-LT'. */
  browser: string
}

/**
 * The language to render, best known first: the operator's override, the launcher's
 * setting, the browser's language, then English.
 */
export function resolveLanguage({ override, server, browser }: LanguageSources): Language {
  if (override) return override
  if (server) return server
  return browser.split('-')[0].toLowerCase() === 'lt' ? 'lt' : 'en'
}

/** This browser's own choice, or null. Storage can be blocked (private browsing). */
export function readStoredLanguage(): Language | null {
  try {
    const stored = window.localStorage.getItem(LANGUAGE_STORAGE_KEY)
    return isLanguage(stored) ? stored : null
  } catch {
    return null
  }
}

/** Save this browser's choice; null forgets it so the launcher or the browser decides again. */
export function writeStoredLanguage(language: Language | null): void {
  try {
    if (language) window.localStorage.setItem(LANGUAGE_STORAGE_KEY, language)
    else window.localStorage.removeItem(LANGUAGE_STORAGE_KEY)
  } catch {
    // Storage is unavailable: the choice then lasts only until the page is closed.
  }
}

/**
 * The launcher's interface language from `GET /api/ui-language`, or null when the
 * server does not say: an older server, a refused token, or no connection. The
 * page renders without it and switches once it arrives.
 */
export async function fetchServerLanguage(token: string | null): Promise<Language | null> {
  try {
    const response = await fetch('/api/ui-language', {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    })
    if (!response.ok) return null
    const body = (await response.json()) as { language?: unknown } | null
    const language = body?.language
    return isLanguage(language) ? language : null
  } catch {
    return null
  }
}
