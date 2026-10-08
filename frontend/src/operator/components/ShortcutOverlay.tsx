/** Always-visible legend of the operator keyboard shortcuts. */
export function ShortcutOverlay() {
  return (
    <div className="operator-shortcuts">
      <span className="operator-shortcuts-label">Shortcuts:</span>
      <kbd>↑↓</kbd> Navigate
      <kbd>Enter</kbd> Approve
      <kbd>Esc</kbd> Clear Selection
    </div>
  )
}
