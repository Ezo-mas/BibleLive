import { useEffect } from 'react'

interface UseOperatorShortcutsOptions {
  /** Enter only acts (and is swallowed) while a pending item is selected. */
  hasSelection: boolean
  /** B only clears (and is swallowed) while something is on the display. */
  canClearDisplay: boolean
  /**
   * False while a modal owns the page (the settings dialog). Approving the
   * staged reference from behind a dialog — or clearing the selection with the
   * Escape that closed it — is exactly the kind of surprise the shortcuts must
   * not cause.
   */
  enabled?: boolean
  onNext: () => void
  onPrevious: () => void
  onApprove: () => void
  onDeselect: () => void
  onClearDisplay: () => void
}

/**
 * Whether the focused element already acts on Enter and the arrow keys itself.
 *
 * A shortcut that fires on top of the focused control does the wrong thing
 * twice over: Enter on "Clear Display" would also approve the selected
 * reference, and the arrow keys inside the book picker would move the pending
 * selection instead of the select's own value.
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
 * Shortcuts stay inert while a text field has focus so typing a token or a
 * search term never moves the pending selection, and while a button, link, or
 * select has focus so those controls answer the key themselves. A queue card is
 * a div with `role="button"`, so it is deliberately not in that list: Enter on
 * a focused card stages it, and Enter again approves the staged reference — the
 * console's own flow.
 *
 * B ("blank", as in a PowerPoint slideshow) clears the display. It is a plain
 * letter, so it stays a letter wherever one is typed (a text field, or a select that
 * jumps to an option), and it leaves Ctrl+B and the like to the browser. A focused
 * button does not block it: after Show, the button keeps focus, and the next thing
 * the operator reaches for is often Clear.
 */
export function useOperatorShortcuts({
  hasSelection,
  canClearDisplay,
  enabled = true,
  onNext,
  onPrevious,
  onApprove,
  onDeselect,
  onClearDisplay,
}: UseOperatorShortcutsOptions): void {
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
          if (hasSelection) {
            e.preventDefault()
            onApprove()
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
  }, [
    hasSelection,
    canClearDisplay,
    enabled,
    onNext,
    onPrevious,
    onApprove,
    onDeselect,
    onClearDisplay,
  ])
}
