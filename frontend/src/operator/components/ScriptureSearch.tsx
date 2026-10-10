import { useEffect, useRef } from 'react'
import type { KeyboardEvent, ReactNode } from 'react'
import { AlertCircleIcon, Button, SearchIcon } from '../../components'
import type { ScriptureSearchState } from '../hooks/useScriptureSearch'
import type { ScriptureResult } from '../scriptureQuery'

export interface ScriptureSearchProps {
  search: ScriptureSearchState
  /** The lookup needs the server, so the box is inert while the socket is down. */
  disabled: boolean
  /**
   * False while the settings dialog owns the page. Typing still works, but the
   * direct-to-display shortcut stands down so a passage cannot reach the
   * projector from behind a modal.
   */
  directEnabled?: boolean
}

/** Render a book name with the matched text marked, when it appears in it. */
function highlighted(result: ScriptureResult): ReactNode {
  if (result.kind !== 'book' || !result.highlight) return result.label
  const { start, length } = result.highlight
  return (
    <>
      {result.label.slice(0, start)}
      <mark className="operator-search-mark">{result.label.slice(start, start + length)}</mark>
      {result.label.slice(start + length)}
    </>
  )
}

/**
 * Find Scripture: type a book name, or a reference.
 *
 * The box takes both halves of the operator's habit in one field. A partial
 * name matches books by substring, abbreviation, and prefix — all from the
 * canon the server sends — and a full reference (`john 3:16`, `ge 1 1-3`,
 * `ps 23`) resolves to the coordinates the server's own lookup takes. Matching
 * is local and debounced; the text of a passage always comes from
 * `/api/lookup`, so the console never carries a Bible of its own.
 *
 * Results list the resolved passage first, or the matching books while the text
 * is still a name — with the verbatim input echoed into "No book matches", so a
 * misspelling is answered rather than guessed at. Picking a passage stages it in
 * Up Next (nothing is displayed: Show is a separate, server-confirmed action);
 * picking a book fills the box with its canonical name so the chapter can be
 * typed next.
 *
 * Enter picks the top row, which is what makes the keyboard flow "type, Enter,
 * Show". The global shortcuts stand down while a text field has focus, so Enter
 * here can never approve a queue item by accident. Shift+Enter on a resolved
 * passage skips Up Next and sends it straight to the display through the same
 * `display_reference` request Show uses; on anything less than a complete
 * reference it does nothing.
 */
export function ScriptureSearch({ search, disabled, directEnabled = true }: ScriptureSearchProps) {
  const {
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
  } = search

  const inputRef = useRef<HTMLInputElement>(null)
  // Set when a click completes the text, so focus returns to the box (with the
  // caret at the end) instead of being lost with the row that was clicked.
  const refocusRef = useRef(false)

  useEffect(() => {
    if (!refocusRef.current) return
    refocusRef.current = false
    const element = inputRef.current
    if (!element) return
    element.focus()
    element.setSelectionRange(element.value.length, element.value.length)
  }, [input])

  function pick(result: ScriptureResult) {
    if (result.kind === 'book') refocusRef.current = true
    choose(result)
  }

  function handleKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key !== 'Enter') return
    // Enter is the fast path through the whole interaction, so it never submits
    // or bubbles anywhere else.
    event.preventDefault()
    if (event.shiftKey) {
      // Shift+Enter shows the resolved passage directly: no lookup, no staging,
      // one request. Stopped as well as prevented, so no global handler can turn
      // one keypress into two actions. Incomplete or invalid input — a book row,
      // an error, the box disconnected, Settings open — sends nothing.
      event.stopPropagation()
      if (disabled || !directEnabled) return
      const best = results[0]
      if (best) displayDirect(best)
      return
    }
    const best = results[0]
    if (best) pick(best)
  }

  const error = query.error ?? lookupError
  const bestBook = results[0]?.kind === 'book' ? results[0] : null
  const listUnavailable = !isLoadingBooks && books.length === 0 && booksError !== null

  let hint = 'Type a book or a reference, for example "gen", "john 3:16", or "ps 23".'
  if (isLoadingBooks) hint = 'Loading the book list…'
  else if (results[0]?.kind === 'reference')
    hint = 'Press Enter to stage it in Up Next, or Shift+Enter to show it directly.'
  else if (bestBook) hint = `Press Enter to pick ${bestBook.label}, then type a chapter.`
  else if (input.trim() && !error) hint = 'Keep typing a book name.'

  return (
    <section className="operator-section operator-search" aria-label="Find scripture">
      <div className="operator-section-header">
        <h2 className="operator-section-title">Find Scripture</h2>
        {isLookingUp && <span className="operator-search-busy">Looking up…</span>}
      </div>

      <div className="operator-section-content">
        <div className="operator-search-field">
          <span className="operator-search-icon" aria-hidden="true">
            <SearchIcon />
          </span>
          <input
            ref={inputRef}
            className="operator-search-input"
            type="text"
            value={input}
            onChange={event => setInput(event.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="gen, john 3:16"
            aria-label="Find scripture by book or reference"
            autoComplete="off"
            spellCheck={false}
            disabled={disabled}
          />
        </div>

        {listUnavailable ? (
          <div className="operator-scripture-unavailable">
            <p className="operator-scripture-error" role="alert">
              <AlertCircleIcon aria-hidden="true" />
              {booksError}
            </p>
            <Button variant="secondary" size="sm" onClick={reloadBooks}>
              Retry
            </Button>
          </div>
        ) : (
          <>
            {results.length > 0 && (
              <ul className="operator-search-results" aria-label="Scripture search results">
                {results.map(result => (
                  <li key={result.id}>
                    <button
                      type="button"
                      className={`operator-search-result is-${result.kind}`}
                      onClick={() => pick(result)}
                      disabled={disabled}
                      // Both rows name what picking them does. The book row needs
                      // the label more than it looks: its name is split by the
                      // highlight element, and the accessible name computed from
                      // the parts drops the marked half ("nesis" for a "ge"
                      // match), so the label states the whole name instead.
                      aria-label={
                        result.kind === 'reference'
                          ? `${result.label} — stage in Up Next`
                          : `${result.label}${result.detail ? `, ${result.detail}` : ''} — select this book`
                      }
                    >
                      <span className="operator-search-result-label">{highlighted(result)}</span>
                      {result.kind === 'reference' && (
                        <span className="operator-search-result-chip">Complete reference</span>
                      )}
                      {result.detail && (
                        <span className="operator-search-result-detail">{result.detail}</span>
                      )}
                    </button>
                  </li>
                ))}
              </ul>
            )}

            {hiddenCount > 0 && (
              <Button variant="ghost" size="sm" fullWidth onClick={showMore}>
                Show {hiddenCount} more
              </Button>
            )}

            {error ? (
              <p className="operator-scripture-error" role="alert">
                <AlertCircleIcon aria-hidden="true" />
                {error}
              </p>
            ) : (
              <p className="operator-setting-hint">{hint}</p>
            )}
          </>
        )}
      </div>
    </section>
  )
}
