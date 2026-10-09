import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { AlertCircleIcon, Button } from '../../components'
import type { ScriptureSearchState } from '../hooks/useScriptureSearch'

export interface ScriptureSearchProps {
  search: ScriptureSearchState
  /** The lookup needs the server, so the form is inert while the socket is down. */
  disabled: boolean
}

/** Ties the header's toggle to the form it opens. */
const PANEL_ID = 'scripture-search-panel'

/** Positive whole number, or null when the field is empty or not a number. */
function parseCount(value: string): number | null {
  const trimmed = value.trim()
  if (!/^\d+$/.test(trimmed)) return null
  const parsed = Number(trimmed)
  return parsed >= 1 ? parsed : null
}

/**
 * Find Scripture: the operator's own way to choose a passage.
 *
 * This is a lookup, not a search engine — the database is a plain verse table
 * with no full-text index, so the form asks for book, chapter, and verse rather
 * than a phrase. Everything it needs comes from the server: the book list (and
 * each book's chapter count) from `/api/books` and the verse text from
 * `/api/lookup`, so the console never carries its own canon or guesses what a
 * passage contains.
 *
 * It stays closed until the operator opens it. A service often runs without a
 * single manual passage, and the sidebar has to spend its height on the live
 * parts; opening the form is also what triggers the book-list request. The form
 * focuses its first field on open so the whole interaction is: open, type the
 * chapter, press Enter.
 *
 * Finding a passage stages it in Up Next and nothing more. It is not queued as a
 * detection and it is not shown until the operator presses Show there, which is
 * a separate, server-confirmed action. Validation the form can do on its own
 * (a chapter beyond the book's length, a reversed range) is reported inline;
 * everything else is the server's answer, shown as it sent it.
 */
export function ScriptureSearch({ search, disabled }: ScriptureSearchProps) {
  const {
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
  } = search

  const [book, setBook] = useState('')
  const [chapter, setChapter] = useState('')
  const [verseStart, setVerseStart] = useState('')
  const [verseEnd, setVerseEnd] = useState('')
  const [formError, setFormError] = useState<string | null>(null)
  const chapterRef = useRef<HTMLInputElement>(null)

  const selected = books.find(candidate => candidate.name === book) ?? null

  // Genesis until the operator picks another book, so the form is usable the
  // moment the list arrives rather than starting on an empty select.
  useEffect(() => {
    if (!book && books.length > 0) setBook(books[0].name)
  }, [book, books])

  // Straight to the chapter box: the book already has a sensible default, and
  // the chapter is what changes most often.
  useEffect(() => {
    if (isOpen) chapterRef.current?.focus()
  }, [isOpen])

  const listUnavailable = isOpen && !isLoadingBooks && books.length === 0
  const formDisabled = disabled || isLoadingBooks || listUnavailable

  function handleSubmit(event: FormEvent) {
    event.preventDefault()
    if (formDisabled) return

    const chapterNumber = parseCount(chapter)
    if (chapterNumber === null) {
      setFormError('Enter a chapter number of 1 or more.')
      return
    }
    if (selected && chapterNumber > selected.chapters) {
      setFormError(
        `${selected.name} has ${selected.chapters} chapter${selected.chapters === 1 ? '' : 's'}.`
      )
      return
    }

    const start = verseStart.trim() === '' ? null : parseCount(verseStart)
    const end = verseEnd.trim() === '' ? null : parseCount(verseEnd)
    if (start === null && verseStart.trim() !== '') {
      setFormError('The starting verse must be a whole number of 1 or more.')
      return
    }
    if (end === null && verseEnd.trim() !== '') {
      setFormError('The ending verse must be a whole number of 1 or more.')
      return
    }
    if (start !== null && end !== null && end < start) {
      setFormError('The ending verse must be at or after the starting verse.')
      return
    }

    setFormError(null)
    clearLookupError()
    // Blank verse boxes mean verse 1, matching how the server reads a
    // chapter-only reference.
    findPassage({ book, chapter: chapterNumber, verseStart: start, verseEnd: end })
  }

  const error = formError ?? lookupError

  return (
    <section className="operator-section operator-scripture" aria-label="Find scripture">
      <div className="operator-section-header">
        <h2 className="operator-section-title">Find Scripture</h2>
        <Button
          variant="ghost"
          size="sm"
          aria-expanded={isOpen}
          aria-controls={PANEL_ID}
          onClick={toggleOpen}
        >
          {isOpen ? 'Close' : 'Open'}
        </Button>
      </div>

      {isOpen && (
        <div className="operator-section-content" id={PANEL_ID}>
          {listUnavailable ? (
            <div className="operator-scripture-unavailable">
              <p className="operator-scripture-error" role="alert">
                {booksError ?? 'The book list is unavailable.'}
              </p>
              <Button variant="secondary" size="sm" onClick={reloadBooks}>
                Retry
              </Button>
            </div>
          ) : (
            <form className="operator-scripture-form" onSubmit={handleSubmit} noValidate>
              <label className="operator-scripture-field">
                <span className="operator-setting-label">Book</span>
                <select
                  className="operator-scripture-select"
                  value={book}
                  onChange={event => {
                    setBook(event.target.value)
                    setFormError(null)
                  }}
                  disabled={formDisabled}
                >
                  {books.map(candidate => (
                    <option key={candidate.name} value={candidate.name}>
                      {candidate.name}
                    </option>
                  ))}
                </select>
              </label>

              <div className="operator-scripture-coordinates">
                <label className="operator-scripture-field">
                  <span className="operator-setting-label">Chapter</span>
                  <input
                    ref={chapterRef}
                    className="operator-scripture-input"
                    type="number"
                    inputMode="numeric"
                    min={1}
                    max={selected?.chapters}
                    value={chapter}
                    onChange={event => {
                      setChapter(event.target.value)
                      setFormError(null)
                    }}
                    disabled={formDisabled}
                  />
                </label>

                <label className="operator-scripture-field">
                  <span className="operator-setting-label">Verse</span>
                  <input
                    className="operator-scripture-input"
                    type="number"
                    inputMode="numeric"
                    min={1}
                    placeholder="1"
                    value={verseStart}
                    onChange={event => {
                      setVerseStart(event.target.value)
                      setFormError(null)
                    }}
                    disabled={formDisabled}
                  />
                </label>

                <label className="operator-scripture-field">
                  <span className="operator-setting-label">To</span>
                  <input
                    className="operator-scripture-input"
                    type="number"
                    inputMode="numeric"
                    min={1}
                    placeholder="—"
                    value={verseEnd}
                    onChange={event => {
                      setVerseEnd(event.target.value)
                      setFormError(null)
                    }}
                    disabled={formDisabled}
                  />
                </label>
              </div>

              <Button
                type="submit"
                variant="primary"
                size="md"
                fullWidth
                disabled={formDisabled || isLookingUp}
              >
                {isLookingUp ? 'Looking up…' : 'Find'}
              </Button>

              {error ? (
                <p className="operator-scripture-error" role="alert">
                  <AlertCircleIcon aria-hidden="true" />
                  {error}
                </p>
              ) : (
                <p className="operator-setting-hint">
                  {isLoadingBooks
                    ? 'Loading the book list…'
                    : 'Blank verses mean verse 1. Finding a passage stages it in Up Next — press Show there to put it on the display.'}
                </p>
              )}
            </form>
          )}
        </div>
      )}
    </section>
  )
}
