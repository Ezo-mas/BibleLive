import { useEffect } from 'react'

interface UseOperatorShortcutsOptions {
  /** Enter only acts (and is swallowed) while a pending item is selected. */
  hasSelection: boolean
  onNext: () => void
  onPrevious: () => void
  onApprove: () => void
  onDeselect: () => void
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
 * Wire the operator console's global keyboard shortcuts.
 *
 * Shortcuts stay inert while a text field has focus so typing a token or a
 * search term never moves the pending selection, and while a button, link, or
 * select has focus so those controls answer the key themselves. A queue card is
 * a div with `role="button"`, so it is deliberately not in that list: Enter on
 * a focused card stages it, and Enter again approves the staged reference — the
 * console's own flow.
 */
export function useOperatorShortcuts({
  hasSelection,
  onNext,
  onPrevious,
  onApprove,
  onDeselect,
}: UseOperatorShortcutsOptions): void {
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
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
  }, [hasSelection, onNext, onPrevious, onApprove, onDeselect])
}
