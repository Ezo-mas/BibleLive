import { useEffect, useId, useRef } from 'react'
import type { KeyboardEvent } from 'react'
import { ThemeToggle, XIcon } from '../../components'
import { DisplaySettings } from './DisplaySettings'
import { AutoApproveControls } from './AutoApproveControls'
import type { Theme } from '../../hooks/useTheme'
import type { DisplayTheme, VerseLimit } from '../../shared/verseText'
import type { AutoMode } from '../../shared/types'

/** Which half of the dialog to open on: the display or the auto-approve block. */
export type SettingsSection = 'display' | 'auto'

export interface SettingsDialogProps {
  open: boolean
  /** The section the dialog opens onto (the compact auto-approve control lands here). */
  section: SettingsSection
  onClose: () => void
  verseLimit: VerseLimit
  displayTheme: DisplayTheme
  /** Settings are server-owned, so they are read-only while disconnected. */
  disabled: boolean
  onVerseLimitChange: (next: VerseLimit) => void
  onThemeChange: (next: DisplayTheme) => void
  autoMode: AutoMode
  onAutoModeChange: (next: AutoMode) => void
  /**
   * The console's own theme. Held by `App` and passed in, because the theme has
   * to be applied on load — the control that changes it now lives behind this
   * dialog and would otherwise only apply it once the dialog was opened.
   */
  appTheme: Theme
  onAppThemeChange: (next: Theme) => void
}

/** Everything inside the dialog that can hold focus, in document order. */
function focusableItems(dialog: HTMLElement): HTMLElement[] {
  return Array.from(
    dialog.querySelectorAll<HTMLElement>(
      'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
    )
  )
}

/**
 * Settings, behind a gear in the header.
 *
 * Display preferences, the app theme, and the auto-approve mode are all
 * configuration the operator sets once and then leaves alone, so they no longer
 * hold a permanent column of the console: the working column gets that space
 * back and the settings keep one home instead of two.
 *
 * Modal on purpose — every control in it changes what the audience or the whole
 * console is using, and a drawer that stayed open would compete with the queue
 * for attention. It traps Tab, closes on Escape and on the explicit close
 * button, and hands focus back to whatever opened it, so the gear-to-settings
 * trip is fine to make with the keyboard alone.
 *
 * Nothing here is console state: the values are the ones the server reported,
 * and every change travels the same WebSocket actions it did when these
 * controls lived in the sidebar. Closing the dialog changes nothing by itself.
 */
export function SettingsDialog({
  open,
  section,
  onClose,
  verseLimit,
  displayTheme,
  disabled,
  onVerseLimitChange,
  onThemeChange,
  autoMode,
  onAutoModeChange,
  appTheme,
  onAppThemeChange,
}: SettingsDialogProps) {
  const titleId = useId()
  const displaySectionId = useId()
  const autoSectionId = useId()
  const shortcutsSectionId = useId()
  const dialogRef = useRef<HTMLDivElement>(null)
  const autoSectionRef = useRef<HTMLElement>(null)

  // Move focus into the dialog while it is open and put it back afterwards, so
  // the keyboard flow never falls back to the top of the page.
  useEffect(() => {
    if (!open) return
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const dialog = dialogRef.current
    const target =
      section === 'auto'
        ? dialog?.querySelector<HTMLElement>('.operator-dialog-auto [aria-pressed="true"]')
        : dialog?.querySelector<HTMLElement>('[data-dialog-close]')
    target?.focus()
    if (section === 'auto') {
      // jsdom has no scrollIntoView; the shortcut is what keeps the tests honest.
      autoSectionRef.current?.scrollIntoView?.({ block: 'nearest' })
    }
    return () => previous?.focus()
  }, [open, section])

  if (!open) return null

  function handleKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === 'Escape') {
      event.preventDefault()
      event.stopPropagation()
      onClose()
      return
    }
    if (event.key !== 'Tab') return

    const items = focusableItems(event.currentTarget)
    if (items.length === 0) return
    const first = items[0]
    const last = items[items.length - 1]
    const active = document.activeElement

    if (event.shiftKey && (active === first || !event.currentTarget.contains(active))) {
      event.preventDefault()
      last.focus()
    } else if (!event.shiftKey && active === last) {
      event.preventDefault()
      first.focus()
    }
  }

  return (
    <div
      className="operator-dialog-overlay"
      onClick={event => {
        // Clicking the backdrop is an escape hatch; clicking inside is not.
        if (event.target === event.currentTarget) onClose()
      }}
    >
      <div
        className="operator-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        ref={dialogRef}
        onKeyDown={handleKeyDown}
      >
        <div className="operator-dialog-header">
          <h2 className="operator-dialog-title" id={titleId}>
            Settings
          </h2>
          <button
            type="button"
            className="operator-dialog-close"
            data-dialog-close
            onClick={onClose}
            aria-label="Close settings"
          >
            <XIcon />
          </button>
        </div>

        <div className="operator-dialog-body">
          <section className="operator-dialog-section" aria-labelledby={displaySectionId}>
            <h3 className="operator-dialog-section-title" id={displaySectionId}>
              Display
            </h3>
            <DisplaySettings
              verseLimit={verseLimit}
              displayTheme={displayTheme}
              disabled={disabled}
              onVerseLimitChange={onVerseLimitChange}
              onThemeChange={onThemeChange}
            />
            <div className="operator-setting">
              <span className="operator-setting-label">App theme</span>
              <ThemeToggle theme={appTheme} onThemeChange={onAppThemeChange} />
              <p className="operator-setting-hint">
                Light, dark, or follow this computer&rsquo;s setting. This affects the operator
                console only.
              </p>
            </div>
          </section>

          <section
            className="operator-dialog-section operator-dialog-auto"
            aria-labelledby={autoSectionId}
            ref={autoSectionRef}
          >
            <h3 className="operator-dialog-section-title" id={autoSectionId}>
              Auto-approve
            </h3>
            <AutoApproveControls
              autoMode={autoMode}
              disabled={disabled}
              onAutoModeChange={onAutoModeChange}
            />
          </section>

          <section
            className="operator-dialog-section"
            aria-labelledby={shortcutsSectionId}
          >
            <h3 className="operator-dialog-section-title" id={shortcutsSectionId}>
              Keyboard Shortcuts
            </h3>
            <ul className="operator-dialog-shortcuts">
              <li>
                <span className="operator-dialog-shortcut-keys">
                  <kbd>↑</kbd>
                  <kbd>↓</kbd>
                </span>
                Move the pending selection
              </li>
              <li>
                <span className="operator-dialog-shortcut-keys">
                  <kbd>Enter</kbd>
                </span>
                Show the staged reference; in Find Scripture, stage the top result
              </li>
              <li>
                <span className="operator-dialog-shortcut-keys">
                  <kbd>Shift</kbd>+<kbd>Enter</kbd>
                </span>
                In Find Scripture, show the resolved passage directly
              </li>
              <li>
                <span className="operator-dialog-shortcut-keys">
                  <kbd>Esc</kbd>
                </span>
                Clear the selection; close this dialog when it is open
              </li>
            </ul>
          </section>
        </div>
      </div>
    </div>
  )
}
