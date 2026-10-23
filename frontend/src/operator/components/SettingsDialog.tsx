import { useEffect, useId, useRef } from 'react'
import type { KeyboardEvent } from 'react'
import { Button, ThemeToggle, XIcon } from '../../components'
import { useI18n } from '../../shared/i18nContext'
import { DisplaySettings } from './DisplaySettings'
import { AutoApproveControls } from './AutoApproveControls'
import { FollowAutoControls } from './FollowAutoControls'
import { InterfaceLanguageSettings } from './InterfaceLanguageSettings'
import { LanguageSettings } from './LanguageSettings'
import type { SpokenLanguageState } from '../hooks/useSpokenLanguage'
import { BibleVersionSettings } from './BibleVersionSettings'
import type { BibleVersionState } from '../hooks/useBibleVersion'
import { IdleScreenSettings } from './IdleScreenSettings'
import type { IdleScreenState } from '../hooks/useIdleScreen'
import type { Theme } from '../../hooks/useTheme'
import type { DisplayTheme, VerseLimit } from '../../shared/verseText'
import type { AutoMode } from '../../shared/types'

/** Which part of the dialog to open on: the display, or the spoken language. */
export type SettingsSection = 'display' | 'language'

export interface SettingsDialogProps {
  open: boolean
  /** The section the dialog opens onto. */
  section: SettingsSection
  onClose: () => void
  verseLimit: VerseLimit
  displayTheme: DisplayTheme
  /** Settings are server-owned, so they are read-only while disconnected. */
  disabled: boolean
  onVerseLimitChange: (next: VerseLimit) => void
  onThemeChange: (next: DisplayTheme) => void
  /** The audio pipeline's spoken language: read and switched through the server. */
  spokenLanguage: SpokenLanguageState
  /** The Bible translation verse text comes from: read and switched through the server. */
  bibleVersion: BibleVersionState
  /** The picture the display shows while nothing is on screen: read and changed through the server. */
  idleScreen: IdleScreenState
  autoMode: AutoMode
  onAutoModeChange: (next: AutoMode) => void
  /** Follow-along auto-show: the server shows the verses a speaker reads on to by itself. */
  followAuto: boolean
  onFollowAutoChange: (next: boolean) => void
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
  spokenLanguage,
  bibleVersion,
  idleScreen,
  autoMode,
  onAutoModeChange,
  followAuto,
  onFollowAutoChange,
  appTheme,
  onAppThemeChange,
}: SettingsDialogProps) {
  const titleId = useId()
  const displaySectionId = useId()
  const consoleSectionId = useId()
  const languageSectionId = useId()
  const autoSectionId = useId()
  const shortcutsSectionId = useId()
  const dialogRef = useRef<HTMLDivElement>(null)
  const languageSectionRef = useRef<HTMLElement>(null)
  const { t } = useI18n()

  // Move focus into the dialog while it is open and put it back afterwards, so
  // the keyboard flow never falls back to the top of the page.
  useEffect(() => {
    if (!open) return
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const dialog = dialogRef.current
    const sectionSelector =
      section === 'language' ? '.operator-dialog-language [aria-pressed="true"]' : null
    // The control for the section asked for, or the close button when it has
    // nothing to focus yet (the language buttons are off until the pipeline answers).
    const target =
      (sectionSelector ? dialog?.querySelector<HTMLElement>(sectionSelector) : null) ??
      dialog?.querySelector<HTMLElement>('[data-dialog-close]')
    target?.focus()
    // jsdom has no scrollIntoView; the optional call is what keeps the tests honest.
    if (section === 'language') languageSectionRef.current?.scrollIntoView?.({ block: 'nearest' })
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
            {t('settings.title')}
          </h2>
          <Button
            variant="ghost"
            iconOnly
            icon={<XIcon />}
            data-dialog-close
            onClick={onClose}
            aria-label={t('settings.close')}
          />
        </div>

        <div className="operator-dialog-body">
          <section className="operator-dialog-section" aria-labelledby={displaySectionId}>
            <h3 className="operator-dialog-section-title" id={displaySectionId}>
              {t('settings.sectionDisplay')}
            </h3>
            <DisplaySettings
              verseLimit={verseLimit}
              displayTheme={displayTheme}
              disabled={disabled}
              onVerseLimitChange={onVerseLimitChange}
              onThemeChange={onThemeChange}
            />
            <IdleScreenSettings state={idleScreen} disabled={disabled} />
          </section>

          <section className="operator-dialog-section" aria-labelledby={consoleSectionId}>
            <h3 className="operator-dialog-section-title" id={consoleSectionId}>
              {t('settings.sectionConsole')}
            </h3>
            <div className="operator-setting">
              <div className="operator-setting-text">
                <span className="operator-setting-label">{t('settings.appTheme')}</span>
                <p className="operator-setting-hint">{t('settings.appThemeHint')}</p>
              </div>
              <ThemeToggle theme={appTheme} onThemeChange={onAppThemeChange} />
            </div>
            <InterfaceLanguageSettings />
          </section>

          <section
            className="operator-dialog-section operator-dialog-language"
            aria-labelledby={languageSectionId}
            ref={languageSectionRef}
          >
            <h3 className="operator-dialog-section-title" id={languageSectionId}>
              {t('settings.sectionAudio')}
            </h3>
            <LanguageSettings state={spokenLanguage} disabled={disabled} />
            <BibleVersionSettings state={bibleVersion} disabled={disabled} />
          </section>

          <section className="operator-dialog-section" aria-labelledby={autoSectionId}>
            <h3 className="operator-dialog-section-title" id={autoSectionId}>
              {t('settings.sectionAuto')}
            </h3>
            <AutoApproveControls
              autoMode={autoMode}
              disabled={disabled}
              onAutoModeChange={onAutoModeChange}
            />
            <FollowAutoControls
              followAuto={followAuto}
              disabled={disabled}
              onFollowAutoChange={onFollowAutoChange}
            />
          </section>

          <section className="operator-dialog-section" aria-labelledby={shortcutsSectionId}>
            <h3 className="operator-dialog-section-title" id={shortcutsSectionId}>
              {t('settings.sectionShortcuts')}
            </h3>
            <ul className="operator-dialog-shortcuts">
              <li>
                <span className="operator-dialog-shortcut-keys">
                  <kbd>↑</kbd>
                  <kbd>↓</kbd>
                </span>
                {t('settings.shortcutMove')}
              </li>
              <li>
                <span className="operator-dialog-shortcut-keys">
                  <kbd>Enter</kbd>
                </span>
                {t('settings.shortcutShow')}
              </li>
              <li>
                <span className="operator-dialog-shortcut-keys">
                  <kbd>Shift</kbd>+<kbd>Enter</kbd>
                </span>
                {t('settings.shortcutShift')}
              </li>
              <li>
                <span className="operator-dialog-shortcut-keys">
                  <kbd>B</kbd>
                </span>
                {t('settings.shortcutClear')}
              </li>
              <li>
                <span className="operator-dialog-shortcut-keys">
                  <kbd>Esc</kbd>
                </span>
                {t('settings.shortcutEsc')}
              </li>
            </ul>
          </section>
        </div>
      </div>
    </div>
  )
}
