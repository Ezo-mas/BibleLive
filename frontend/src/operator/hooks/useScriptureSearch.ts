import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { fetchBooks, lookupPassage } from '../passageApi'
import type { PassageQuery } from '../passageApi'
import {
  BOOK_RESULT_LIMIT,
  bookLabel,
  buildResults,
  localizeBooks,
  parseScriptureQuery,
} from '../scriptureQuery'
import type { ScriptureResult } from '../scriptureQuery'
import type { Passage, ScriptureBook } from '../../shared/types'
import { useI18n } from '../../shared/i18nContext'

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
  /**
   * Look a resolved passage row up and hand it to the plan. Like `choose`, nothing
   * is displayed; a book row does nothing.
   */
  planResult: (result: ScriptureResult) => void
  /**
   * Look a passage up and stage it in Up Next, for callers that already have
   * coordinates. With `notify`, a failure goes to `onLookupError` (a notice)
   * instead of the search box, for callers that are not looking at it.
   */
  stagePassage: (request: PassageQuery, notify?: boolean) => void
  /**
   * The rows for what is in the box right now. Results follow the debounced text,
   * so a key that acts on a row (Enter shows it) must read this instead, or it
   * could act on what was typed a moment ago.
   */
  resolveNow: () => ScriptureResult[]
  /** Drop any lookup still in flight, so a late answer cannot stage over a newer action. */
  cancelLookup: () => void
  /**
   * The start of the top row's verse, so the operator can tell it is the right
   * passage before choosing it. Null while the top row is a book or has no
   * reference; `text` is null while it loads.
   */
  preview: { id: string; text: string | null } | null
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
  onDirectDisplay: (request: PassageQuery) => void,
  bibleVersion: string | null = null,
  onPlan: (passage: Passage) => void = () => {},
  onLookupError: (message: string) => void = () => {}
): ScriptureSearchState {
  const { t } = useI18n()
  const [input, setInputState] = useState('')
  const [debouncedInput, setDebouncedInput] = useState('')
  const [canon, setCanon] = useState<ScriptureBook[]>([])
  const [booksError, setBooksError] = useState<string | null>(null)
  const [isLoadingBooks, setIsLoadingBooks] = useState(false)
  const [showAllBooks, setShowAllBooks] = useState(false)
  const [isLookingUp, setIsLookingUp] = useState(false)
  const [lookupError, setLookupError] = useState<string | null>(null)
  const [previewState, setPreviewState] = useState<{
    id: string
    text: string | null
    error: string | null
  } | null>(null)

  // Latest callback without re-running the book load every render.
  const onPassageRef = useRef(onPassage)
  onPassageRef.current = onPassage
  // The direct-display path, held the same way: the search panel fires it from
  // a key handler, which must not re-subscribe anything to do so.
  const onDirectDisplayRef = useRef(onDirectDisplay)
  onDirectDisplayRef.current = onDirectDisplay
  const onPlanRef = useRef(onPlan)
  onPlanRef.current = onPlan
  const onLookupErrorRef = useRef(onLookupError)
  onLookupErrorRef.current = onLookupError
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
      const loaded = await fetchBooks(token, t)
      if (requestId !== booksIdRef.current) return
      setCanon(loaded)
    } catch (error) {
      if (requestId !== booksIdRef.current) return
      setCanon([])
      setBooksError(error instanceof Error ? error.message : t('search.booksFailed'))
    } finally {
      if (requestId === booksIdRef.current) setIsLoadingBooks(false)
    }
  }, [token, t])

  const ensureBooks = useCallback(() => {
    if (booksRequestedRef.current) return
    booksRequestedRef.current = true
    void loadBooks()
  }, [loadBooks])

  const reloadBooks = useCallback(() => {
    booksRequestedRef.current = true
    void loadBooks()
  }, [loadBooks])

  // The book names belong to the loaded Bible translation, so a switch makes the
  // list the search holds stale. Reload it, but only if it has been asked for and
  // the version really changed (learning the first version is not a change).
  const seenVersionRef = useRef<string | null>(null)
  useEffect(() => {
    const previous = seenVersionRef.current
    if (bibleVersion) seenVersionRef.current = bibleVersion
    if (previous && bibleVersion && previous !== bibleVersion && booksRequestedRef.current) {
      void loadBooks()
    }
  }, [bibleVersion, loadBooks])

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

  // The canon as the loaded translation names it. Only labels and search spellings
  // change; every request below still names a book by its canonical `name`.
  const books = useMemo(() => localizeBooks(canon), [canon])
  const booksRef = useRef(books)
  booksRef.current = books

  const query = useMemo(
    () => parseScriptureQuery(debouncedInput, books, t),
    [debouncedInput, books, t]
  )

  const { results, hiddenCount } = useMemo(
    () => buildResults(query, showAllBooks ? Number.POSITIVE_INFINITY : BOOK_RESULT_LIMIT, t),
    [query, showAllBooks, t]
  )

  const showMore = useCallback(() => setShowAllBooks(true), [])

  const resolveNow = useCallback(
    () =>
      input === debouncedInput
        ? results
        : buildResults(parseScriptureQuery(input, books, t), BOOK_RESULT_LIMIT, t).results,
    [input, debouncedInput, results, books, t]
  )

  // The top row's passage, looked up as soon as it is a complete reference, so the
  // operator sees how the verse begins before staging or showing it. Read-only,
  // and superseded the same way a lookup is.
  const top = results[0]
  const previewId =
    top?.kind === 'reference' && top.coordinates
      ? `${top.book.name}|${top.coordinates.chapter}|${top.coordinates.verseStart}|${top.coordinates.verseEnd}`
      : null
  const topRef = useRef(top)
  topRef.current = top
  const previewRequestRef = useRef(0)
  useEffect(() => {
    const requestId = ++previewRequestRef.current
    const row = topRef.current
    if (previewId === null || !row || row.kind !== 'reference' || !row.coordinates) {
      setPreviewState(null)
      return
    }
    const coordinates = row.coordinates
    setPreviewState({ id: previewId, text: null, error: null })
    void (async () => {
      try {
        const passage = await lookupPassage(
          {
            book: row.book.name,
            chapter: coordinates.chapter,
            verseStart: coordinates.verseStart,
            verseEnd: coordinates.verseEnd,
          },
          token,
          t
        )
        if (requestId !== previewRequestRef.current) return
        setPreviewState({
          id: previewId,
          text: passage.verses[0]?.text ?? passage.text,
          error: null,
        })
      } catch (error) {
        if (requestId !== previewRequestRef.current) return
        setPreviewState({
          id: previewId,
          text: null,
          error: error instanceof Error ? error.message : t('search.lookupFailed'),
        })
      }
    })()
  }, [previewId, token, t, bibleVersion])

  const findPassage = useCallback(
    (request: PassageQuery, deliver: (passage: Passage) => void, notify = false) => {
      const requestId = ++lookupIdRef.current
      setLookupError(null)
      setIsLookingUp(true)
      void (async () => {
        try {
          const passage = await lookupPassage(request, token, t)
          if (requestId !== lookupIdRef.current) return
          // The preview reads in the loaded translation's names like everything else; the
          // lookup itself was made, and is shown again later, by canonical name.
          const label = booksRef.current.find(book => book.name === passage.book)?.label
          deliver(label ? { ...passage, book_label: label } : passage)
        } catch (error) {
          if (requestId !== lookupIdRef.current) return
          const message = error instanceof Error ? error.message : t('search.lookupFailed')
          if (notify) onLookupErrorRef.current(message)
          else setLookupError(message)
        } finally {
          if (requestId === lookupIdRef.current) setIsLookingUp(false)
        }
      })()
    },
    [token, t]
  )

  const stagePassage = useCallback(
    (request: PassageQuery, notify = false) =>
      findPassage(request, passage => onPassageRef.current(passage), notify),
    [findPassage]
  )

  const cancelLookup = useCallback(() => {
    lookupIdRef.current += 1
    setIsLookingUp(false)
  }, [])

  const choose = useCallback(
    (result: ScriptureResult) => {
      if (result.kind === 'reference' && result.coordinates) {
        stagePassage({
          book: result.book.name,
          chapter: result.coordinates.chapter,
          verseStart: result.coordinates.verseStart,
          verseEnd: result.coordinates.verseEnd,
        })
        return
      }
      // A book is not a passage: picking one completes the text so its chapter
      // can be typed. Nothing is looked up, staged, or sent.
      setInputState(`${bookLabel(result.book)} `)
      setShowAllBooks(false)
      setLookupError(null)
    },
    [stagePassage]
  )

  // Adding to the plan resolves the passage first, so only a real one is planned,
  // then empties the box for the next one.
  const planResult = useCallback(
    (result: ScriptureResult) => {
      if (result.kind !== 'reference' || !result.coordinates) return
      findPassage(
        {
          book: result.book.name,
          chapter: result.coordinates.chapter,
          verseStart: result.coordinates.verseStart,
          verseEnd: result.coordinates.verseEnd,
        },
        passage => {
          onPlanRef.current(passage)
          setInputState('')
          setDebouncedInput('')
        }
      )
    },
    [findPassage]
  )

  // Enter in the search box: skip the lookup and the staging entirely and
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
    planResult,
    resolveNow,
    stagePassage,
    cancelLookup,
    preview:
      previewState && previewState.id === previewId && !previewState.error
        ? { id: previewState.id, text: previewState.text }
        : null,
    isLookingUp,
    lookupError:
      lookupError ?? (previewState?.id === previewId ? previewState?.error : null) ?? null,
  }
}
