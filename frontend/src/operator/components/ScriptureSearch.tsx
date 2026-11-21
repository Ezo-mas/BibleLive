import { useEffect, useRef } from 'react'
import type { KeyboardEvent, ReactNode } from 'react'
import { AlertCircleIcon, Button, PlusIcon, SearchIcon } from '../../components'
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
 * misspelling is answered rather than guessed at. Picking a book fills the box
 * with its canonical name so the chapter can be typed next.
 *
 * Enter (or a click on the passage row) shows the resolved passage on the
 * display: one coordinates-only `display_reference` request, the same one every
 * manual Show sends. The preview of how the verse begins is what makes that safe —
 * the operator sees it is the right passage before pressing the key. On a book row
 * Enter completes the name instead. Ctrl+Enter (or the + button) adds the
 * passage to the plan, for the operator lining verses up ahead of time; Shift+Enter
 * does the same, so the old plan key can never put a passage on screen. The global
 * shortcuts stand down while a text field has focus, so none of these can approve
 * a queue item by accident.
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
    if (result.kind === 'book') {
      refocusRef.current = true
      choose(result)
      return
    }
    // A passage row shows it: not from a disconnected box or from behind Settings.
    if (!disabled && directEnabled) choose(result)
  }

  function handleKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key !== 'Enter') return
    // Enter is the fast path through the whole interaction, so it never submits
    // or bubbles anywhere else: stopped as well as prevented, so no global handler
    // can turn one keypress into two actions.
    event.preventDefault()
    event.stopPropagation()
    // A held key repeats: the first press acts, and its repeats send nothing more.
    if (event.repeat) return
    // The row for what is in the box now, not for what it said a debounce ago.
    const best = resolveNow()[0]
    if (!best) return
    if (best.kind === 'book') {
      pick(best)
      return
    }
    if (event.ctrlKey || event.metaKey || event.shiftKey) {
      // Ctrl+Enter plans the resolved passage (Shift+Enter, the old plan key, too).
      if (!disabled) planResult(best)
      return
    }
    // Enter shows the resolved passage. The box disconnected or Settings open —
    // sends nothing.
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
                    <li key={result.id} className={`operator-search-item is-${result.kind}`}>
                      <button
                        type="button"
                        className={`btn operator-search-result ${
                          result.kind === 'reference' ? 'btn-ghost' : 'btn-secondary'
                        }`}
                        onClick={event => {
                          // The second click of a double-click is not a second Show.
                          if (result.kind === 'reference' && event.detail > 1) return
                          pick(result)
                        }}
                        onKeyDown={event => {
                          if (
                            result.kind !== 'reference' ||
                            event.defaultPrevented ||
                            event.key !== 'Enter' ||
                            !(event.ctrlKey || event.metaKey || event.shiftKey)
                          ) {
                            return
                          }
                          event.preventDefault()
                          event.stopPropagation()
                          if (!disabled) planResult(result)
                          // This row goes once the passage is planned: keep focus in the box,
                          // not on the page, where the next key would reach the global shortcuts.
                          inputRef.current?.focus()
                        }}
                        disabled={disabled}
                        // Both rows name what picking them does. The book row needs
                        // the label more than it looks: its name is split by the
                        // highlight element, and the accessible name computed from
                        // the parts drops the marked half ("nesis" for a "ge"
                        // match), so the label states the whole name instead.
                        aria-label={
                          result.kind === 'reference'
                            ? t('search.showAria', { label: result.label })
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
                          variant="primary"
                          size="sm"
                          className="operator-search-plan"
                          iconOnly
                          icon={<PlusIcon />}
                          onClick={() => planResult(result)}
                          disabled={disabled}
                          aria-label={t('search.addToPlanAria', { label: result.label })}
                          title={t('search.addToPlan')}
                        />
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
