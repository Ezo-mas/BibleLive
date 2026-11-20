import { useEffect } from 'react'

interface UseOperatorShortcutsOptions {
  /**
   * Enter only acts (and is swallowed) while a row is highlighted in a list that is
   * on screen: a highlight in Pending does nothing while the History tab covers it.
   */
  canShow: boolean
  /** B only clears (and is swallowed) while something is on the display. */
  canClearDisplay: boolean
  /**
   * False while a modal owns the page (the settings dialog). Showing the
   * highlighted row from behind a dialog — or clearing the highlight with the
   * Escape that closed it — is exactly the kind of surprise the shortcuts must
   * not cause.
   */
  enabled?: boolean
  onNext: () => void
  onPrevious: () => void
  onShow: () => void
  onDeselect: () => void
  onClearDisplay: () => void
}

/**
 * Whether the focused element already acts on Enter and the arrow keys itself.
 *
 * A shortcut that fires on top of the focused control does the wrong thing
 * twice over: Enter on "Clear Display" would also show the highlighted
 * reference, and the arrow keys inside the book picker would move the
 * highlight instead of the select's own value.
 */
function handlesItsOwnKeys(target: EventTarget | null): boolean {
  return (
    target instanceof HTMLInputElement ||
    target instanceof HTMLTextAreaElement ||
    target instanceof HTMLSelectElement ||
    target instanceof HTMLButtonElement ||
    target instanceof HTMLAnchorElement
  )
}

/**
 * Whether the focused element takes a typed letter itself: a text field types it and a
 * select jumps to the option starting with it.
 */
function takesLetters(target: EventTarget | null): boolean {
  return (
    target instanceof HTMLInputElement ||
    target instanceof HTMLTextAreaElement ||
    target instanceof HTMLSelectElement ||
    (target instanceof HTMLElement && target.isContentEditable)
  )
}

/**
 * Wire the operator console's global keyboard shortcuts.
 *
 * ↑ / ↓ move the highlight (the one keyboard cursor, in Pending or Plan), Enter
 * shows the highlighted row, Escape drops the highlight. They stay inert while a
 * text field has focus, so typing a token or a search term never moves the
 * highlight, and while a button, link, or select has focus, so those controls
 * answer the key themselves. A row is a div with `role="button"`, so it is
 * deliberately not in that list: focus on a row highlights it, and Enter shows it.
 *
 * B ("blank", as in a PowerPoint slideshow) clears the display. It is a plain
 * letter, so it stays a letter wherever one is typed (a text field, or a select that
 * jumps to an option), and it leaves Ctrl+B and the like to the browser. A focused
 * button does not block it: after Show, the button keeps focus, and the next thing
 * the operator reaches for is often Clear.
 */
export function useOperatorShortcuts({
  canShow,
  canClearDisplay,
  enabled = true,
  onNext,
  onPrevious,
  onShow,
  onDeselect,
  onClearDisplay,
}: UseOperatorShortcutsOptions): void {
  useEffect(() => {
    function preventRepeatedActivation(event: KeyboardEvent) {
      if (
        event.key === 'Enter' &&
        event.repeat &&
        event.target instanceof Element &&
        event.target.matches(
          'button, [role="button"], [role="menuitem"], [role="menuitemcheckbox"]'
        )
      ) {
        event.preventDefault()
      }
    }

    window.addEventListener('keydown', preventRepeatedActivation, true)
    return () => window.removeEventListener('keydown', preventRepeatedActivation, true)
  }, [])

  useEffect(() => {
    if (!enabled) return

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key.toLowerCase() === 'b' && !e.ctrlKey && !e.metaKey && !e.altKey) {
        if (!e.repeat && canClearDisplay && !takesLetters(e.target)) {
          e.preventDefault()
          onClearDisplay()
        }
        return
      }

      if (handlesItsOwnKeys(e.target)) {
        return
      }

      switch (e.key) {
        case 'ArrowDown':
          e.preventDefault()
          onNext()
          break
        case 'ArrowUp':
          e.preventDefault()
          onPrevious()
          break
        case 'Enter':
          // A held key repeats: one Enter is one Show. Only a plain Enter shows: Ctrl/Shift+Enter
          // means "add to the plan" in Find Scripture, so a stray one must never go on air.
          if (canShow && !e.repeat && !e.ctrlKey && !e.metaKey && !e.shiftKey && !e.altKey) {
            e.preventDefault()
            onShow()
          }
          break
        case 'Escape':
          e.preventDefault()
          onDeselect()
          break
      }
    }

    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [canShow, canClearDisplay, enabled, onNext, onPrevious, onShow, onDeselect, onClearDisplay])
}
