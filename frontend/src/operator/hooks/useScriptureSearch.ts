import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { fetchBooks, lookupPassage } from '../passageApi'
import type { PassageQuery } from '../passageApi'
import { BOOK_RESULT_LIMIT, buildResults, parseScriptureQuery } from '../scriptureQuery'
import type { ScriptureResult } from '../scriptureQuery'
import type { Passage, ScriptureBook } from '../../shared/types'

/** How long typing settles before the text is matched against the canon. */
export const SEARCH_DEBOUNCE_MS = 250

export interface ScriptureSearchState {
  /** What the operator has typed, echoed back for the controlled input. */
  input: string
  setInput: (value: string) => void
  /** Canonical books for matching, from the server (never a local copy). */
  books: ScriptureBook[]
  booksError: string | null
  isLoadingBooks: boolean
  /** Try the book list again after a failure. */
  reloadBooks: () => void
  /** The debounced reading of the input: book matches plus any reference. */
  query: ReturnType<typeof parseScriptureQuery>
  /** Rows to show: the resolved passage first, else matching books. */
  results: ScriptureResult[]
  /** Book matches hidden behind "Show more". */
  hiddenCount: number
  showMore: () => void
  /**
   * Pick a row: a passage is looked up and staged in Up Next, a book completes
   * the text so its chapter can be typed. Nothing is ever displayed from here.
   */
  choose: (result: ScriptureResult) => void
  /**
   * Send a resolved passage row straight to the display without staging it.
   * Only reference rows carry coordinates; calling it with a book row is a
   * no-op. The caller decides when this is allowed (valid input, dialog
   * closed); the request itself is the same `display_reference` Show sends.
   */
  displayDirect: (result: ScriptureResult) => void
  isLookingUp: boolean
  /** Why the last lookup failed, for the panel to show inline. */
  lookupError: string | null
}

/**
 * Text search over the server's canon, and the lookup that stages a passage.
 *
 * This is the door to the manual side of the console, and it is deliberately a
 * read-only one: both requests are GETs, and a successful lookup only tells the
 * caller what the passage is — staging it is the caller's decision, and showing
 * it is a separate, server-confirmed action. Nothing here touches the pending
 * queue or the display.
 *
 * The book list is fetched the first time the operator types something, so a
 * console that never searches makes no extra request; matching then happens
 * locally (see `scriptureQuery.ts`) and only the final lookup travels.
 *
 * A superseded lookup is discarded rather than applied, so a slow first request
 * cannot overwrite the result of a later one.
 */
export function useScriptureSearch(
  token: string | null,
  onPassage: (passage: Passage) => void,
  onDirectDisplay: (request: PassageQuery) => void
): ScriptureSearchState {
  const [input, setInputState] = useState('')
  const [debouncedInput, setDebouncedInput] = useState('')
  const [books, setBooks] = useState<ScriptureBook[]>([])
  const [booksError, setBooksError] = useState<string | null>(null)
  const [isLoadingBooks, setIsLoadingBooks] = useState(false)
  const [showAllBooks, setShowAllBooks] = useState(false)
  const [isLookingUp, setIsLookingUp] = useState(false)
  const [lookupError, setLookupError] = useState<string | null>(null)

  // Latest callback without re-running the book load every render.
  const onPassageRef = useRef(onPassage)
  onPassageRef.current = onPassage
  // The direct-display path, held the same way: the search panel fires it from
  // a key handler, which must not re-subscribe anything to do so.
  const onDirectDisplayRef = useRef(onDirectDisplay)
  onDirectDisplayRef.current = onDirectDisplay
  // Identifies the newest request of each kind; anything older is ignored when
  // it lands.
  const lookupIdRef = useRef(0)
  const booksIdRef = useRef(0)
  // Whether the canon has been asked for. Set by the first keystroke, cleared
  // when the token changes so a rejected session can try again.
  const booksRequestedRef = useRef(false)

  // Short debounce: results keep up with typing without a request or a re-match
  // per keystroke.
  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedInput(input), SEARCH_DEBOUNCE_MS)
    return () => window.clearTimeout(timer)
  }, [input])

  // A new token (a re-entered one after a rejection) means the previous failure
  // should not block the next attempt; already-loaded books are kept.
  useEffect(() => {
    booksRequestedRef.current = false
    setBooksError(null)
  }, [token])

  const loadBooks = useCallback(async () => {
    const requestId = ++booksIdRef.current
    setIsLoadingBooks(true)
    setBooksError(null)
    try {
      const loaded = await fetchBooks(token)
      if (requestId !== booksIdRef.current) return
      setBooks(loaded)
    } catch (error) {
      if (requestId !== booksIdRef.current) return
      setBooks([])
      setBooksError(error instanceof Error ? error.message : 'Could not load the book list.')
    } finally {
      if (requestId === booksIdRef.current) setIsLoadingBooks(false)
    }
  }, [token])

  const ensureBooks = useCallback(() => {
    if (booksRequestedRef.current) return
    booksRequestedRef.current = true
    void loadBooks()
  }, [loadBooks])

  const reloadBooks = useCallback(() => {
    booksRequestedRef.current = true
    void loadBooks()
  }, [loadBooks])

  const setInput = useCallback(
    (value: string) => {
      setInputState(value)
      // The previous lookup's failure belonged to other text.
      setLookupError(null)
      setShowAllBooks(false)
      if (value.trim()) ensureBooks()
    },
    [ensureBooks]
  )

  const query = useMemo(() => parseScriptureQuery(debouncedInput, books), [debouncedInput, books])

  const { results, hiddenCount } = useMemo(
    () => buildResults(query, showAllBooks ? Number.POSITIVE_INFINITY : BOOK_RESULT_LIMIT),
    [query, showAllBooks]
  )

  const showMore = useCallback(() => setShowAllBooks(true), [])

  const findPassage = useCallback(
    (request: PassageQuery) => {
      const requestId = ++lookupIdRef.current
      setLookupError(null)
      setIsLookingUp(true)
      void (async () => {
        try {
          const passage = await lookupPassage(request, token)
          if (requestId !== lookupIdRef.current) return
          onPassageRef.current(passage)
        } catch (error) {
          if (requestId !== lookupIdRef.current) return
          setLookupError(error instanceof Error ? error.message : 'Could not look that up.')
        } finally {
          if (requestId === lookupIdRef.current) setIsLookingUp(false)
        }
      })()
    },
    [token]
  )

  const choose = useCallback(
    (result: ScriptureResult) => {
      if (result.kind === 'reference' && result.coordinates) {
        findPassage({
          book: result.book.name,
          chapter: result.coordinates.chapter,
          verseStart: result.coordinates.verseStart,
          verseEnd: result.coordinates.verseEnd,
        })
        return
      }
      // A book is not a passage: picking one completes the text so its chapter
      // can be typed. Nothing is looked up, staged, or sent.
      setInputState(`${result.book.name} `)
      setShowAllBooks(false)
      setLookupError(null)
    },
    [findPassage]
  )

  // Shift+Enter in the search box: skip the lookup and the staging entirely and
  // hand the resolved coordinates to the display path. A book row has no
  // coordinates, so there is nothing to send and this does nothing.
  const displayDirect = useCallback((result: ScriptureResult) => {
    if (result.kind === 'reference' && result.coordinates) {
      onDirectDisplayRef.current({
        book: result.book.name,
        chapter: result.coordinates.chapter,
        verseStart: result.coordinates.verseStart,
        verseEnd: result.coordinates.verseEnd,
      })
    }
  }, [])

  return {
    input,
    setInput,
    books,
    booksError,
    isLoadingBooks,
    reloadBooks,
    query,
    results,
    hiddenCount,
    showMore,
    choose,
    displayDirect,
    isLookingUp,
    lookupError,
  }
}
