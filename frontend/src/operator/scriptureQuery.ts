import type { ScriptureBook } from '../shared/types'
import { defaultTranslator } from '../shared/i18n'
import type { Translator } from '../shared/i18n'

/**
 * Turn typed scripture text into book matches and a reference the server can
 * resolve.
 *
 * Everything here is pure so it can be unit-tested without a server or a
 * socket. It owns no book data: the canon, including each book's abbreviation
 * and the alternative name forms the parser accepts, comes from `/api/books`
 * and is matched by substring/prefix. Nothing in this module talks to the
 * network, and nothing here displays anything — a resolved reference is still
 * staged in Up Next and shown by the operator's own Show action.
 *
 * Why a parser lives here at all: the server's `core/verse_parser.py` reads
 * *spoken* sermon text, where it deliberately guesses at misspellings and
 * missing words. Typed input must not be guessed at — a query it cannot take
 * literally is reported to the operator instead of being resolved to a nearby
 * passage — so this module stays strict and lets the server validate and
 * resolve the coordinates it produces.
 */

/** How many book rows the results list shows before offering "Show more". */
export const BOOK_RESULT_LIMIT = 12

/** Chapter and verse bounds, in the shape the lookup endpoint takes. */
export interface ScriptureCoordinates {
  chapter: number
  /** Null means "the chapter as the server reads it" (it opens at verse 1). */
  verseStart: number | null
  verseEnd: number | null
}

/** Where in a book's name to highlight the typed text, when it appears there. */
export interface BookHighlight {
  start: number
  length: number
}

/** One book the typed text matches, best match first. */
export interface BookMatch {
  book: ScriptureBook
  /** Null when the match came from an abbreviation or alias instead of the name. */
  highlight: BookHighlight | null
}

/** What the operator's text means, or why it cannot be used. */
export interface ScriptureQuery {
  /** The book-name portion of the text, e.g. "1 cor". */
  bookText: string
  /** The canonical book the name portion resolves to, or null. */
  book: ScriptureBook | null
  /** Books whose name, abbreviation, or variant matches, best first. */
  matches: BookMatch[]
  /** Parsed chapter/verses, or null when only a book name was typed. */
  coordinates: ScriptureCoordinates | null
  /** Why the text cannot be used as typed, shown inline; null when it is fine. */
  error: string | null
  /** The reference as it will be looked up, e.g. "Genesis 1:1-3"; null without one. */
  label: string | null
  /** True when the text names a book and a chapter the console can request. */
  isComplete: boolean
}

/** A row in the results list: a resolved passage, or a book to drill into. */
export interface ScriptureResult {
  kind: 'reference' | 'book'
  /** Stable key; book rows are keyed by canonical position. */
  id: string
  /** What the row reads, e.g. "Genesis 1:1-3" or "Genesis". */
  label: string
  /** Secondary text, or null when the row needs none. */
  detail: string | null
  book: ScriptureBook
  /** Coordinates to stage; null on a book row. */
  coordinates: ScriptureCoordinates | null
  highlight: BookHighlight | null
}

/** Drop accents, so "Pradžios" is found by "pradzios" and typing never needs ž or ė. */
function stripMarks(text: string): string {
  return text.normalize('NFD').replace(/[̀-ͯ]/g, '')
}

/** Lowercase letters and digits only, so "1 cor.", "1Cor", and "1 cor" match. */
function fold(text: string): string {
  return stripMarks(text.toLowerCase()).replace(/[^a-z0-9]/g, '')
}

/** The name to show for a book: the loaded translation's if it has one, else the canonical. */
export function bookLabel(book: ScriptureBook): string {
  return book.label ?? book.name
}

/**
 * The canon with the loaded translation's own book names applied, for display and
 * matching.
 *
 * Only `label` and the extra search spellings change: `name` stays canonical,
 * because it is what every request to the server uses. A book the translation
 * has no name for is returned unchanged.
 */
export function localizeBooks(books: ScriptureBook[]): ScriptureBook[] {
  return books.map(book => {
    if (!book.local_name) return book
    return {
      ...book,
      label: book.local_name,
      variants: [...(book.variants ?? []), ...(book.local_variants ?? [])],
    }
  })
}

/**
 * Where the folded query sits inside the shown name, for highlighting.
 *
 * Returns null rather than a guess: a match through an abbreviation ("gn" for
 * Genesis) or an alias ("canticles") has no matching text in the name itself.
 */
function highlightRange(name: string, needle: string): BookHighlight | null {
  // One entry per character, so positions line up with `name` after accents go.
  const chars = name.split('').map(character => stripMarks(character.toLowerCase()))
  const direct = chars.join('').indexOf(needle)
  if (chars.join('').length === name.length && direct >= 0) {
    return { start: direct, length: needle.length }
  }

  // Separator-insensitive pass: "1cor" highlights across "1 Corinthians".
  for (let start = 0; start < name.length; start += 1) {
    let taken = 0
    let index = start
    while (index < name.length && taken < needle.length) {
      const character = chars[index]
      index += 1
      if (!/^[a-z0-9]$/.test(character)) continue
      if (character !== needle[taken]) break
      taken += 1
    }
    if (taken === needle.length) return { start, length: index - start }
  }
  return null
}

/**
 * Books matching the typed name portion, best match first.
 *
 * Ranking is what keeps "john 3:16" off 1 John: an exact book name beats an
 * exact abbreviation or variant, which beats a name prefix, and so on, with
 * canonical order breaking ties. A partial match never hides its alternatives —
 * every other match is returned too, in the same list.
 */
export function matchBooks(bookText: string, books: ScriptureBook[]): BookMatch[] {
  const needle = fold(bookText)
  if (!needle) return []

  const scored: { match: BookMatch; score: number; order: number }[] = []

  for (const book of books) {
    // Both the shown name and the canonical one count as the book's name, so an
    // operator on a Lithuanian console can still type "john" or "gen".
    const names = [fold(bookLabel(book)), fold(book.name)]
    const aliases = [book.abbrev ?? '', ...(book.variants ?? [])]
      .map(fold)
      .filter(alias => alias.length > 0)

    let score: number | null = null
    if (names.includes(needle)) score = 0
    // An exact alias outranks a mere name prefix: "jono" is John's own short form
    // and must beat Jonah ("Jonos"), whose name only starts with it.
    else if (aliases.includes(needle)) score = 1
    else if (names.some(name => name.startsWith(needle))) score = 2
    else if (aliases.some(alias => alias.startsWith(needle))) score = 3
    else if (names.some(name => name.includes(needle))) score = 4
    else if (aliases.some(alias => alias.includes(needle))) score = 5

    if (score === null) continue
    scored.push({
      match: { book, highlight: highlightRange(bookLabel(book), needle) },
      score,
      order: book.order ?? 0,
    })
  }

  return scored
    .sort((left, right) => left.score - right.score || left.order - right.order)
    .map(entry => entry.match)
}

/**
 * Split the typed text into a book-name portion and a coordinate portion.
 *
 * The split happens at the first run of digits whose preceding text names a
 * book, which is what tells "1 john 3:16" (book "1 john", chapter 3) apart from
 * "ge 1 1" (book "ge"). A leading number that belongs to a book name has no
 * text before it, so it is skipped.
 */
function splitQuery(text: string, namesABook: (candidate: string) => boolean): string {
  for (const run of text.matchAll(/\d+/g)) {
    const start = run.index ?? 0
    const candidate = text.slice(0, start).trim()
    if (candidate && namesABook(candidate)) return candidate
  }
  return text.trim()
}

const RANGE_SEPARATOR = /[-–—]/

/** Parse the coordinate portion. Numbers only; anything else is reported. */
function parseCoordinates(
  book: ScriptureBook,
  coordinatesText: string,
  t: Translator
): { coordinates: ScriptureCoordinates | null; error: string | null } {
  if (!coordinatesText) return { coordinates: null, error: null }

  const name = bookLabel(book)

  if (/[a-z]/i.test(coordinatesText)) {
    return {
      coordinates: null,
      error: t('scripture.useChapterVerse', { name }),
    }
  }

  const numbers = coordinatesText
    .split(/[^0-9]+/)
    .filter(part => part.length > 0)
    .map(Number)
  const separators = coordinatesText.match(/[^0-9]+/g) ?? []

  if (numbers.length === 0) return { coordinates: null, error: null }

  if (numbers.length > 3) {
    return {
      coordinates: null,
      error: t('scripture.tooManyNumbers', { name }),
    }
  }

  if (numbers.length === 2 && RANGE_SEPARATOR.test(separators[0] ?? '')) {
    return {
      coordinates: null,
      error: t('scripture.ambiguousRange', { name, from: numbers[0], to: numbers[1] }),
    }
  }

  const [chapter, verseStart, verseEnd] = numbers

  if (chapter < 1) {
    return { coordinates: null, error: t('scripture.chapterMin') }
  }
  if (chapter > book.chapters) {
    return {
      coordinates: null,
      error: t('scripture.chapterLimit', { name, count: book.chapters }),
    }
  }

  const start = verseStart ?? null
  const end = verseEnd ?? null
  if ((start !== null && start < 1) || (end !== null && end < 1)) {
    return { coordinates: null, error: t('scripture.verseMin') }
  }
  if (start !== null && end !== null && end < start) {
    return {
      coordinates: null,
      error: t('scripture.rangeOrder'),
    }
  }

  return {
    coordinates: {
      chapter,
      verseStart: start,
      // A one-verse range is the same as naming the verse once.
      verseEnd: end !== null && end === start ? null : end,
    },
    error: null,
  }
}

/** Format what will be requested, in the shape the rest of the console uses. */
function referenceLabel(book: ScriptureBook, coordinates: ScriptureCoordinates): string {
  let label = `${bookLabel(book)} ${coordinates.chapter}`
  if (coordinates.verseStart !== null) {
    label += `:${coordinates.verseStart}`
    if (coordinates.verseEnd !== null && coordinates.verseEnd !== coordinates.verseStart) {
      label += `-${coordinates.verseEnd}`
    }
  }
  return label
}

/**
 * Read the operator's text against the canon.
 *
 * Never guesses a book: if nothing matches, `book` stays null and the results
 * list stays empty with a message saying so. An incomplete number ("Genesis"
 * with no chapter) is not an error — it is just not stageable yet.
 */
export function parseScriptureQuery(
  text: string,
  books: ScriptureBook[],
  t: Translator = defaultTranslator
): ScriptureQuery {
  const trimmed = text.trim()
  const empty: ScriptureQuery = {
    bookText: '',
    book: null,
    matches: [],
    coordinates: null,
    error: null,
    label: null,
    isComplete: false,
  }
  if (!trimmed) return empty
  // Nothing to match against yet. Reporting "no book matches" while the canon
  // is still loading would be a false alarm, and the panel says it is loading.
  if (books.length === 0) return { ...empty, bookText: trimmed }

  const namesABook = (candidate: string) => matchBooks(candidate, books).length > 0
  const bookText = splitQuery(trimmed, namesABook)
  const coordinatesText =
    bookText.length < trimmed.length ? trimmed.slice(bookText.length).trim() : ''

  const matches = matchBooks(bookText, books)
  const book = matches[0]?.book ?? null

  // A digits-only string is someone part-way through typing a book number;
  // saying "no book matches" there would be noise, so it stays quiet.
  const hasLetters = /[a-z]/i.test(trimmed)
  if (!book) {
    return {
      ...empty,
      bookText,
      matches: [],
      error: hasLetters ? t('scripture.noBook', { book: bookText }) : null,
    }
  }

  const { coordinates, error } = parseCoordinates(book, coordinatesText, t)
  return {
    bookText,
    book,
    matches,
    coordinates,
    error,
    label: coordinates ? referenceLabel(book, coordinates) : null,
    isComplete: coordinates !== null && error === null,
  }
}

/**
 * Rows for the results list: the resolved passage first, or the matching books
 * when the text is not (yet) a complete reference.
 *
 * A complete reference is the whole answer — the operator asked for one passage,
 * and listing the book they named underneath it would be noise.
 */
export function buildResults(
  query: ScriptureQuery,
  limit: number = BOOK_RESULT_LIMIT,
  t: Translator = defaultTranslator
): { results: ScriptureResult[]; hiddenCount: number } {
  if (query.book && query.coordinates && !query.error) {
    return {
      results: [
        {
          kind: 'reference',
          id: `reference-${query.book.order}`,
          label: query.label ?? bookLabel(query.book),
          detail: query.coordinates.verseStart === null ? t('search.wholeChapter') : null,
          book: query.book,
          coordinates: query.coordinates,
          highlight: null,
        },
      ],
      hiddenCount: 0,
    }
  }

  const shown = query.matches.slice(0, limit)
  return {
    results: shown.map(match => ({
      kind: 'book' as const,
      id: `book-${match.book.order}`,
      label: bookLabel(match.book),
      detail: t('scripture.chapterCount', { count: match.book.chapters }),
      book: match.book,
      coordinates: null,
      highlight: match.highlight,
    })),
    hiddenCount: Math.max(0, query.matches.length - shown.length),
  }
}
