import { useCallback, useEffect, useRef, useState } from 'react'
import { fetchBooks, lookupPassage } from '../passageApi'
import type { PassageQuery } from '../passageApi'
import type { Passage, ScriptureBook } from '../../shared/types'

export interface ScriptureSearchState {
  /** Whether the operator has the Find Scripture form open. */
  isOpen: boolean
  toggleOpen: () => void
  /** Canonical books for the picker, from the server (never a local copy). */
  books: ScriptureBook[]
  booksError: string | null
  isLoadingBooks: boolean
  /** Try the book list again after a failure. */
  reloadBooks: () => void
  /** Look a passage up; on success it is staged in Up Next. */
  findPassage: (query: PassageQuery) => void
  isLookingUp: boolean
  /** Why the last lookup failed, for the form to show inline. */
  lookupError: string | null
  clearLookupError: () => void
}

/**
 * Load the canonical book list and resolve passages the operator asks for.
 *
 * This is the door to the manual side of the console, and it is deliberately a
 * read-only one: both requests are GETs, and a successful lookup only tells the
 * caller what the passage is — staging it is the caller's decision, and showing
 * it is a separate, server-confirmed action. Nothing here touches the pending
 * queue or the display.
 *
 * The panel starts closed and the book list is only fetched once it is opened:
 * loading it at console start would run a request for a control the operator may
 * never touch this service, and the sidebar has better uses for the height.
 *
 * A superseded lookup is discarded rather than applied, so a slow first request
 * cannot overwrite the result of a later one.
 */
export function useScriptureSearch(
  token: string | null,
  onPassage: (passage: Passage) => void
): ScriptureSearchState {
  const [isOpen, setIsOpen] = useState(false)
  const [books, setBooks] = useState<ScriptureBook[]>([])
  const [booksError, setBooksError] = useState<string | null>(null)
  const [isLoadingBooks, setIsLoadingBooks] = useState(false)
  const [isLookingUp, setIsLookingUp] = useState(false)
  const [lookupError, setLookupError] = useState<string | null>(null)

  // Latest callback without re-running the book load every render.
  const onPassageRef = useRef(onPassage)
  onPassageRef.current = onPassage
  // Identifies the newest lookup; anything older is ignored when it lands.
  const lookupIdRef = useRef(0)
  const booksIdRef = useRef(0)

  const loadBooks = useCallback(
    async (signal?: AbortSignal) => {
      const requestId = ++booksIdRef.current
      setIsLoadingBooks(true)
      setBooksError(null)
      try {
        const loaded = await fetchBooks(token, signal)
        if (requestId !== booksIdRef.current) return
        setBooks(loaded)
      } catch (error) {
        if (signal?.aborted || requestId !== booksIdRef.current) return
        setBooks([])
        setBooksError(error instanceof Error ? error.message : 'Could not load the book list.')
      } finally {
        if (requestId === booksIdRef.current) setIsLoadingBooks(false)
      }
    },
    [token]
  )

  // Load the first time the form is opened, and again if the token changes (a
  // rejected token would have failed the first attempt). The abort stops a
  // response landing after the panel closes or unmounts.
  useEffect(() => {
    if (!isOpen) return
    const controller = new AbortController()
    void loadBooks(controller.signal)
    return () => controller.abort()
  }, [isOpen, loadBooks])

  const toggleOpen = useCallback(() => setIsOpen(prev => !prev), [])

  const reloadBooks = useCallback(() => {
    void loadBooks()
  }, [loadBooks])

  const findPassage = useCallback(
    (query: PassageQuery) => {
      const requestId = ++lookupIdRef.current
      setLookupError(null)
      setIsLookingUp(true)
      void (async () => {
        try {
          const passage = await lookupPassage(query, token)
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

  const clearLookupError = useCallback(() => setLookupError(null), [])

  return {
    isOpen,
    toggleOpen,
    books,
    booksError,
    isLoadingBooks,
    reloadBooks,
    findPassage,
    isLookingUp,
    lookupError,
    clearLookupError,
  }
}
