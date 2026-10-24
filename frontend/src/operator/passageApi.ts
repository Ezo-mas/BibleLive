import type { BibleReference, BooksResponse, Passage, ScriptureBook } from '../shared/types'
import { defaultTranslator } from '../shared/i18n'
import type { Translator } from '../shared/i18n'
import { ApiError, authHeaders, readFailure, request } from './apiClient'

/** A passage the operator wants to look up. Null bounds mean "let the server decide". */
export interface PassageQuery {
  book: string
  chapter: number
  verseStart: number | null
  verseEnd: number | null
}

/** Load the canonical book list the search form is built from. */
export async function fetchBooks(
  token: string | null,
  t: Translator = defaultTranslator
): Promise<ScriptureBook[]> {
  const response = await request(
    '/api/books',
    { headers: authHeaders(token) },
    t('api.booksUnreachable')
  )
  if (!response.ok) throw new ApiError(response.status, await readFailure(response, undefined, t))

  const data = (await response.json()) as BooksResponse
  return Array.isArray(data?.books) ? data.books : []
}

/** Resolve a book/chapter/verse passage to the text the display would show. */
export async function lookupPassage(
  query: PassageQuery,
  token: string | null,
  t: Translator = defaultTranslator
): Promise<Passage> {
  const params = new URLSearchParams({ book: query.book, chapter: String(query.chapter) })
  if (query.verseStart !== null) params.set('verse_start', String(query.verseStart))
  if (query.verseEnd !== null) params.set('verse_end', String(query.verseEnd))
  // Only the endpoints the server exposes; nothing here can name a file or SQL.
  const url = `/api/lookup?${params.toString()}`

  const response = await request(url, { headers: authHeaders(token) }, t('api.lookupUnreachable'))
  if (!response.ok) throw new ApiError(response.status, await readFailure(response, undefined, t))
  return (await response.json()) as Passage
}

/**
 * Shape a lookup result like a reference so Up Next can preview it with the same
 * markup a queued detection uses.
 *
 * The ID is local and derived: the server assigns the real ID if and when the
 * passage is displayed, so nothing here can be mistaken for a queued reference.
 */
export function passageToReference(passage: Passage): BibleReference {
  return {
    id: `manual-${passage.book}-${passage.chapter}-${passage.verse_start}-${passage.verse_end}`,
    book: passage.book,
    book_label: passage.book_label ?? null,
    chapter: passage.chapter,
    verse_start: passage.verse_start,
    verse_end: passage.verse_end,
    confidence: null,
    inferred: false,
    text: passage.text,
    verses: passage.verses,
    source: 'manual',
    created_at: new Date().toISOString(),
  }
}
