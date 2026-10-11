import type { BibleReference, BooksResponse, Passage, ScriptureBook } from '../shared/types'

/** A passage the operator wants to look up. Null bounds mean "let the server decide". */
export interface PassageQuery {
  book: string
  chapter: number
  verseStart: number | null
  verseEnd: number | null
}

/**
 * A lookup that failed, carrying the server's own explanation when it sent one.
 *
 * `status` is 0 for a request that never reached the server (offline, aborted by
 * the browser), which is the one case the operator needs to be told apart from a
 * rejection: "check the reference" is wrong advice when the network is down.
 */
export class PassageApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'PassageApiError'
    this.status = status
  }
}

/** Manual passages are operator-only, so lookups carry the operator token. */
function authHeaders(token: string | null): HeadersInit {
  return token ? { Authorization: `Bearer ${token}` } : {}
}

/**
 * Turn an unsuccessful response into one line for the operator.
 *
 * FastAPI sends a string detail for our own rejections (the readable messages
 * from the passage resolver) and a list of issues for parameter validation;
 * both are worth showing as-is rather than replacing with a generic failure.
 */
async function readFailure(response: Response): Promise<string> {
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
  if (response.status === 401) return 'Not authorized. Sign in again with your operator token.'
  return `Lookup failed (HTTP ${response.status}).`
}

/** Load the canonical book list the search form is built from. */
export async function fetchBooks(token: string | null): Promise<ScriptureBook[]> {
  let response: Response
  try {
    response = await fetch('/api/books', { headers: authHeaders(token) })
  } catch {
    throw new PassageApiError(0, 'Could not reach the server to load the book list.')
  }
  if (!response.ok) throw new PassageApiError(response.status, await readFailure(response))

  const data = (await response.json()) as BooksResponse
  return Array.isArray(data?.books) ? data.books : []
}

/** Resolve a book/chapter/verse passage to the text the display would show. */
export async function lookupPassage(query: PassageQuery, token: string | null): Promise<Passage> {
  const params = new URLSearchParams({ book: query.book, chapter: String(query.chapter) })
  if (query.verseStart !== null) params.set('verse_start', String(query.verseStart))
  if (query.verseEnd !== null) params.set('verse_end', String(query.verseEnd))
  // Only the endpoints the server exposes; nothing here can name a file or SQL.
  const url = `/api/lookup?${params.toString()}`

  let response: Response
  try {
    response = await fetch(url, { headers: authHeaders(token) })
  } catch {
    throw new PassageApiError(0, 'Could not reach the server to look up that passage.')
  }
  if (!response.ok) throw new PassageApiError(response.status, await readFailure(response))
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
