import { useEffect, useState } from 'react'
import { useTheme } from '../hooks/useTheme'
import { useOperatorState } from './hooks/useOperatorState'
import { useOperatorShortcuts } from './hooks/useOperatorShortcuts'
import { LivePanel } from './components/LivePanel'
import { NextPanel } from './components/NextPanel'
import { PendingQueue } from './components/PendingQueue'
import { HistoryPanel } from './components/HistoryPanel'
import { ScriptureSearch } from './components/ScriptureSearch'
import { AutoApproveCompact } from './components/AutoApproveCompact'
import { SettingsDialog } from './components/SettingsDialog'
import type { SettingsSection } from './components/SettingsDialog'
import { OperatorHeader } from './components/OperatorHeader'
import { LanguageChip } from './components/LanguageChip'
import { OperatorActions } from './components/OperatorActions'
import { ConnectionBanner } from './components/ConnectionBanner'
import { FeedbackNotice } from './components/FeedbackNotice'
import { TokenPrompt } from './components/TokenPrompt'
import '../styles/tokens.css'
import '../styles/global.css'
import './operator.css'

/**
 * Operator console.
 *
 * Composes the page from the operator state hook and the presentational
 * components; all server state and actions live in `useOperatorState` and all
 * keyboard handling in `useOperatorShortcuts`.
 *
 * The working column reads top to bottom as the service runs: LIVE is what the
 * audience is looking at, NEXT is the scripture being prepared for it, then the
 * pending queue with the action row. The utility column on the right holds the
 * three things that are consulted rather than operated: scripture search, the
 * auto-approve mode, and recent history.
 *
 * Preferences — verses shown, display background, app theme, auto-approve — all
 * live in one settings dialog behind the header's gear. Keeping them out of the
 * page is what gives On Display and the queue their space, and the compact
 * auto-approve chip is the single reminder on screen that the server may be
 * displaying detections on its own.
 *
 * Scripture reaches the display by one of two doors, and they stay separate all
 * the way down. A pending reference is approved by ID through the server's
 * approval path; a passage the operator finds by hand — or picks from history to
 * show again — is staged in NEXT and sent as coordinates for the server to
 * resolve itself. Search only ever stages: Show is the operator's own action.
 */
export default function App() {
  const operator = useOperatorState()
  // The console's own light/dark/system setting. Applied here rather than only
  // in the settings dialog's control, so a stored choice is in effect from the
  // first paint instead of waiting for the dialog to be opened.
  const { theme, setTheme } = useTheme()
  const [settingsSection, setSettingsSection] = useState<SettingsSection>('display')
  const [settingsOpen, setSettingsOpen] = useState(false)

  useOperatorShortcuts({
    hasSelection: operator.selectedId !== null,
    // The dialog owns the keyboard while it is open (see useOperatorShortcuts).
    enabled: !settingsOpen,
    onNext: operator.selectNext,
    onPrevious: operator.selectPrevious,
    onApprove: operator.approveSelected,
    onDeselect: operator.deselect,
  })

  // The pipeline can be started, restarted, or switched from a terminal while the
  // console is open, so look again whenever the dialog that shows and changes the
  // language is opened — the reading the operator acts on is then current.
  const { refresh: refreshSpokenLanguage } = operator.spokenLanguage
  const isConnected = operator.isConnected
  useEffect(() => {
    if (settingsOpen && isConnected) refreshSpokenLanguage()
  }, [settingsOpen, isConnected, refreshSpokenLanguage])

  function openSettings(section: SettingsSection) {
    setSettingsSection(section)
    setSettingsOpen(true)
  }

  if (operator.needsToken) {
    return (
      <TokenPrompt
        authFailed={operator.authFailed}
        tokenInput={operator.tokenInput}
        onTokenInputChange={operator.setTokenInput}
        onSubmit={operator.submitToken}
      />
    )
  }

  return (
    <div className="operator-app">
      <OperatorHeader
        connectionState={operator.connectionState}
        displayConnected={operator.displayConnected}
        showLogout={operator.isAuthenticated}
        onLogout={operator.logout}
        onOpenSettings={() => openSettings('display')}
        language={
          <LanguageChip
            language={operator.spokenLanguage.language}
            onOpenSettings={() => openSettings('language')}
          />
        }
        // Action feedback lives in the header so it can never cover the actions
        // or the queue; it is still the same single-slot live region.
        feedback={<FeedbackNotice notice={operator.notice} onDismiss={operator.dismissNotice} />}
      />
      {/* A closed socket disables every control below, so say why instead of
          leaving the operator to guess. Reachable once the session has
          connected: before that, `needsToken` owns the page. */}
      {operator.status === 'closed' && <ConnectionBanner onReconnect={operator.reconnect} />}
      <main className="operator-main">
        <div className="operator-main-column">
          <div className="operator-preview-row">
            <LivePanel
              reference={operator.currentDisplay}
              verseLimit={operator.verseLimit}
              displayTheme={operator.displayTheme}
            />

            <NextPanel
              reference={operator.nextReference}
              source={operator.nextSource}
              verseLimit={operator.verseLimit}
              canShow={operator.canShowNext}
              onShow={operator.showNext}
              onDiscard={operator.clearNext}
            />
          </div>

          <PendingQueue
            pending={operator.pending}
            count={operator.pendingCount}
            selectedId={operator.selectedId}
            onSelect={operator.selectPending}
          >
            <OperatorActions
              canApprove={operator.canApprove}
              canClearDisplay={operator.canClearDisplay}
              canClearPending={operator.canClearPending}
              flash={operator.flash}
              onApprove={operator.approveSelected}
              onClearDisplay={operator.clearDisplay}
              onClearPending={operator.clearPendingQueue}
            />
          </PendingQueue>
        </div>

        <aside className="operator-sidebar" aria-label="Utilities">
          <ScriptureSearch
            search={operator.search}
            disabled={!operator.isConnected}
            // Shift+Enter in the box shows a passage directly; not from behind
            // the settings modal.
            directEnabled={!settingsOpen}
          />

          <AutoApproveCompact
            autoMode={operator.autoMode}
            onOpenSettings={() => openSettings('auto')}
          />

          <HistoryPanel
            history={operator.history}
            count={operator.historyCount}
            selectedId={operator.selectedHistoryId}
            onSelect={operator.selectHistory}
          />
        </aside>
      </main>

      <SettingsDialog
        open={settingsOpen}
        section={settingsSection}
        onClose={() => setSettingsOpen(false)}
        verseLimit={operator.verseLimit}
        displayTheme={operator.displayTheme}
        disabled={!operator.isConnected}
        onVerseLimitChange={operator.setDisplayVerseLimit}
        onThemeChange={operator.setDisplayBackground}
        spokenLanguage={operator.spokenLanguage}
        autoMode={operator.autoMode}
        onAutoModeChange={operator.setAutoApproveMode}
        appTheme={theme}
        onAppThemeChange={setTheme}
      />
    </div>
  )
}
