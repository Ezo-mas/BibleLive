import { useEffect, useRef } from 'react'
import type { KeyboardEvent, ReactNode } from 'react'
import { AlertCircleIcon, Button, SearchIcon } from '../../components'
import { useI18n } from '../../shared/i18nContext'
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
 * Enter on a resolved passage shows it on the display: no staging, one
 * `display_reference` request, the same one Show sends. The preview of how the
 * verse begins is what makes that safe — the operator sees it is the right
 * passage before pressing the key. On a book row Enter completes the name instead,
 * so the chapter can be typed next. Shift+Enter (or the Plan button) adds the
 * passage to the plan, for the operator lining verses up ahead of time; Ctrl+Enter
 * stages it in Up Next to preview it before Show. The global shortcuts stand down
 * while a text field has focus, so none of these can approve a queue item by
 * accident.
 *
 * A resolved passage shows how its first verse begins, looked up as the reference
 * becomes complete, so a wrong book or verse is seen before anything is staged.
 */
export function ScriptureSearch({ search, disabled, directEnabled = true }: ScriptureSearchProps) {
  const { t } = useI18n()
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
    planResult,
    resolveNow,
    preview,
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
    // The row for what is in the box now, not for what it said a debounce ago.
    const best = resolveNow()[0]
    if (event.shiftKey) {
      // Shift+Enter plans the resolved passage; a book row or no result does nothing.
      event.stopPropagation()
      if (disabled || !best) return
      planResult(best)
      return
    }
    if (event.ctrlKey || event.metaKey) {
      // Ctrl+Enter stages in Up Next (or completes a book name), as Enter once did.
      event.stopPropagation()
      if (best) pick(best)
      return
    }
    if (!best) return
    if (best.kind === 'book') {
      pick(best)
      return
    }
    // Enter shows the resolved passage directly: no staging, one request. Stopped
    // as well as prevented, so no global handler can turn one keypress into two
    // actions. The box disconnected or Settings open — sends nothing.
    event.stopPropagation()
    if (disabled || !directEnabled) return
    displayDirect(best)
  }

  const error = query.error ?? lookupError
  const bestBook = results[0]?.kind === 'book' ? results[0] : null
  const listUnavailable = !isLoadingBooks && books.length === 0 && booksError !== null

  let hint = t('search.hintDefault')
  if (isLoadingBooks) hint = t('search.hintLoading')
  else if (results[0]?.kind === 'reference') hint = t('search.hintReference')
  else if (bestBook) hint = t('search.hintBook', { book: bestBook.label })
  else if (input.trim() && !error) hint = t('search.hintKeepTyping')

  return (
    <section className="operator-section operator-search" aria-label={t('search.sectionAria')}>
      <div className="operator-section-header">
        <h2 className="operator-section-title">{t('search.title')}</h2>
        {isLookingUp && <span className="operator-search-busy">{t('search.looking')}</span>}
      </div>

      <div className="operator-section-content">
        <div className="operator-search-field">
          <span className="operator-search-icon" aria-hidden="true">
            <SearchIcon />
          </span>
          <input
            ref={inputRef}
            className="input operator-search-input"
            type="text"
            value={input}
            onChange={event => setInput(event.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={t('search.placeholder')}
            aria-label={t('search.label')}
            autoComplete="off"
            spellCheck={false}
            disabled={disabled}
          />
        </div>

        {/* Hint, results and errors open under the bar while it has focus. */}
        <div className="operator-search-drop">
          {listUnavailable ? (
            <div className="operator-scripture-unavailable">
              <p className="operator-scripture-error" role="alert">
                <AlertCircleIcon aria-hidden="true" />
                {booksError}
              </p>
              <Button variant="secondary" size="sm" onClick={reloadBooks}>
                {t('search.retry')}
              </Button>
            </div>
          ) : (
            <>
              {results.length > 0 && (
                <ul className="operator-search-results" aria-label={t('search.resultsLabel')}>
                  {results.map(result => (
                    <li key={result.id} className="operator-search-item">
                      <button
                        type="button"
                        className={`btn btn-secondary operator-search-result is-${result.kind}${
                          result.kind === 'reference' ? ' is-on' : ''
                        }`}
                        onClick={() => pick(result)}
                        disabled={disabled}
                        // Both rows name what picking them does. The book row needs
                        // the label more than it looks: its name is split by the
                        // highlight element, and the accessible name computed from
                        // the parts drops the marked half ("nesis" for a "ge"
                        // match), so the label states the whole name instead.
                        aria-label={
                          result.kind === 'reference'
                            ? t('search.stageAria', { label: result.label })
                            : t('search.bookAria', {
                                name: `${result.label}${result.detail ? `, ${result.detail}` : ''}`,
                              })
                        }
                      >
                        <span className="operator-search-result-label">{highlighted(result)}</span>
                        {result.detail && (
                          <span className="operator-search-result-detail">{result.detail}</span>
                        )}
                        {result.kind === 'reference' && preview && (
                          <span className="operator-search-result-preview">
                            {preview.text ?? t('search.previewLoading')}
                          </span>
                        )}
                      </button>
                      {result.kind === 'reference' && (
                        <Button
                          variant="secondary"
                          size="sm"
                          className="operator-search-plan"
                          onClick={() => planResult(result)}
                          disabled={disabled}
                          aria-label={t('search.addToPlanAria', { label: result.label })}
                        >
                          {t('search.addToPlan')}
                        </Button>
                      )}
                    </li>
                  ))}
                </ul>
              )}

              {hiddenCount > 0 && (
                <Button variant="secondary" size="sm" fullWidth onClick={showMore}>
                  {t('search.showMore', { count: hiddenCount })}
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
      </div>
    </section>
  )
}
