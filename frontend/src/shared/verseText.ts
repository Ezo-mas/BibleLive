import type { BibleReference, DisplayTheme, VerseLimit, VerseText } from './types'

/** Default number of verses shown at once on the display. */
export const DEFAULT_VERSE_LIMIT = 3

/** Allowed display sizes, chosen by the operator console. */
export const VERSE_LIMIT_OPTIONS = [3, 5, 10] as const

export type { VerseLimit, DisplayTheme }

/** localStorage key for the operator-chosen verse limit. */
export const VERSE_LIMIT_STORAGE_KEY = 'biblelive_display_verse_limit'

/**
 * Coerce a verse limit received from the server, falling back to the default.
 *
 * Settings arrive over the WebSocket, so an older or newer server may send a
 * value this build does not know. Bad input must never blank the display.
 */
export function coerceVerseLimit(value: unknown): VerseLimit {
  if (value === 'all') return 'all'
  if (typeof value === 'number' && (VERSE_LIMIT_OPTIONS as readonly number[]).includes(value)) {
    return value
  }
  return DEFAULT_VERSE_LIMIT
}

/**
 * Return the verses to display, capped at `limit`.
 *
 * Prefers the server's per-verse list. When it is missing (older server, or text
 * submitted by the audio pipeline), falls back to the joined `text` field as a
 * single entry so the display still renders something.
 */
export function selectVerses(
  ref: BibleReference | null,
  limit: VerseLimit = DEFAULT_VERSE_LIMIT
): VerseText[] {
  if (!ref) return []

  const all =
    ref.verses && ref.verses.length > 0
      ? ref.verses
      : ref.text
        ? [{ verse: ref.verse_start ?? 1, text: ref.text }]
        : []

  if (limit === 'all') return all
  return all.slice(0, limit)
}

/** How many verses are hidden because of the current limit. */
export function hiddenVerseCount(ref: BibleReference | null, limit: VerseLimit): number {
  const shown = selectVerses(ref, limit)
  const total =
    ref && ref.verses && ref.verses.length > 0 ? ref.verses.length : ref && ref.text ? 1 : 0
  return Math.max(0, total - shown.length)
}

/** Read a stored verse limit, ignoring unknown or malformed values. */
export function readStoredVerseLimit(storage?: Storage): VerseLimit {
  try {
    const raw = storage?.getItem(VERSE_LIMIT_STORAGE_KEY)
    if (raw === 'all') return 'all'
    const parsed = Number(raw)
    const isOption = (VERSE_LIMIT_OPTIONS as readonly number[]).includes(parsed)
    return isOption ? (parsed as VerseLimit) : DEFAULT_VERSE_LIMIT
  } catch {
    // Private browsing modes can throw on localStorage access.
    return DEFAULT_VERSE_LIMIT
  }
}

/** Persist a verse limit; failures (private mode, quota) are non-fatal. */
export function writeStoredVerseLimit(limit: VerseLimit, storage?: Storage): void {
  try {
    storage?.setItem(VERSE_LIMIT_STORAGE_KEY, String(limit))
  } catch {
    // Ignore: the limit still applies for this session.
  }
}

/** Background the display shows unless the operator picks light. */
export const DEFAULT_DISPLAY_THEME = 'dark'

/** Themes the operator console offers for the display. */
export const DISPLAY_THEME_OPTIONS = ['dark', 'light'] as const

/** localStorage key for the operator-chosen display theme. */
export const DISPLAY_THEME_STORAGE_KEY = 'biblelive_display_theme'

/**
 * Coerce a display theme received from the server, falling back to the default.
 *
 * A projector with an unknown background is a worse outcome than the default,
 * so anything unexpected resolves to dark.
 */
export function coerceDisplayTheme(value: unknown): DisplayTheme {
  return (DISPLAY_THEME_OPTIONS as readonly string[]).includes(value as string)
    ? (value as DisplayTheme)
    : DEFAULT_DISPLAY_THEME
}

/** Read a stored display theme, ignoring unknown or malformed values. */
export function readStoredDisplayTheme(storage?: Storage): DisplayTheme {
  try {
    return coerceDisplayTheme(storage?.getItem(DISPLAY_THEME_STORAGE_KEY))
  } catch {
    // Private browsing modes can throw on localStorage access.
    return DEFAULT_DISPLAY_THEME
  }
}

/** Persist a display theme; failures (private mode, quota) are non-fatal. */
export function writeStoredDisplayTheme(theme: DisplayTheme, storage?: Storage): void {
  try {
    storage?.setItem(DISPLAY_THEME_STORAGE_KEY, theme)
  } catch {
    // Ignore: the theme still applies for this session.
  }
}
