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
 * Wire the operator console's global keyboard shortcuts.
 *
 * Shortcuts stay inert while a text field has focus so typing a token or a
 * search term never moves the pending selection.
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
      // Only handle shortcuts when not focused on an input
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) {
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
