import { useEffect, useState } from 'react'
import { useTheme } from '../hooks/useTheme'
import { useOperatorState } from './hooks/useOperatorState'
import { useOperatorShortcuts } from './hooks/useOperatorShortcuts'
import { LivePanel } from './components/LivePanel'
import { NextPanel } from './components/NextPanel'
import { PendingQueue } from './components/PendingQueue'
import { FollowAlongHint } from './components/FollowAlongHint'
import { HistoryPanel } from './components/HistoryPanel'
import { ScriptureSearch } from './components/ScriptureSearch'
import { AutoApproveCompact } from './components/AutoApproveCompact'
import { SettingsDialog } from './components/SettingsDialog'
import type { SettingsSection } from './components/SettingsDialog'
import { OperatorHeader } from './components/OperatorHeader'
import { LanguageChip } from './components/LanguageChip'
import { ClearDisplayButton, QueueFooter } from './components/OperatorActions'
import { ConnectionBanner } from './components/ConnectionBanner'
import { PipelineBanner } from './components/PipelineBanner'
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
 * The workspace reads like a switcher: ON DISPLAY is what the audience is looking
 * at, UP NEXT is the scripture being prepared for it (and the one filled button,
 * Show on display, that sends it), and recent history sits below. The pending
 * queue of detections has the column on the right. Find Scripture is a command
 * bar in the header, so it is one keystroke away from anywhere.
 *
 * Preferences — verses shown, display background, app theme, auto-approve — all
 * live in one settings dialog behind the header's gear. Keeping them out of the
 * page is what gives the stages and the queue their space, and the auto-approve
 * line under ON DISPLAY is the single reminder on screen that the server may be
 * displaying detections on its own.
 *
 * Scripture reaches the display by one of two doors, and they stay separate all
 * the way down. A pending reference is approved by ID through the server's
 * approval path; a passage the operator finds by hand — or picks from history to
 * show again — is staged in UP NEXT and sent as coordinates for the server to
 * resolve itself. Search only ever stages: Show on display is the operator's own
 * action.
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
  const { refresh: refreshBibleVersion } = operator.bibleVersion
  const isConnected = operator.isConnected
  useEffect(() => {
    if (settingsOpen && isConnected) {
      refreshSpokenLanguage()
      refreshBibleVersion()
    }
  }, [settingsOpen, isConnected, refreshSpokenLanguage, refreshBibleVersion])

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
            stale={operator.pipeline?.status === 'offline'}
          />
        }
        search={
          <ScriptureSearch
            search={operator.search}
            disabled={!operator.isConnected}
            // Shift+Enter in the box shows a passage directly; not from behind
            // the settings modal.
            directEnabled={!settingsOpen}
          />
        }
        // Action feedback hangs from the header so it can never cover the stages
        // or the queue; it is still the same single-slot live region.
        feedback={<FeedbackNotice notice={operator.notice} onDismiss={operator.dismissNotice} />}
      />
      {/* A closed socket disables every control below, so say why instead of
          leaving the operator to guess. Reachable once the session has
          connected: before that, `needsToken` owns the page. */}
      {operator.status === 'closed' && <ConnectionBanner onReconnect={operator.reconnect} />}
      {/* An empty queue cannot tell a quiet sermon from a dead microphone. */}
      <PipelineBanner status={operator.pipeline} />
      <main className="operator-main">
        <div className="operator-workspace">
          <div className="operator-stage-row">
            <LivePanel
              reference={operator.currentDisplay}
              verseLimit={operator.verseLimit}
              displayTheme={operator.displayTheme}
            >
              <div className="operator-stage-footer">
                <ClearDisplayButton
                  canClearDisplay={operator.canClearDisplay}
                  flash={operator.flash}
                  onClearDisplay={operator.clearDisplay}
                />
                <AutoApproveCompact
                  autoMode={operator.autoMode}
                  onOpenSettings={() => openSettings('auto')}
                />
              </div>
            </LivePanel>

            <NextPanel
              reference={operator.nextReference}
              source={operator.nextSource}
              verseLimit={operator.verseLimit}
              displayTheme={operator.displayTheme}
              canShow={operator.canShowNext}
              flashing={operator.flash === 'approve'}
              onShow={operator.showNext}
              onDiscard={operator.clearNext}
            />
          </div>

          <FollowAlongHint
            hint={operator.followAlong}
            canShow={operator.canShowFollowAlong}
            onShow={operator.showFollowAlong}
          />

          <HistoryPanel
            history={operator.history}
            count={operator.historyCount}
            selectedId={operator.selectedHistoryId}
            onSelect={operator.selectHistory}
          />
        </div>

        <PendingQueue
          pending={operator.pending}
          count={operator.pendingCount}
          selectedId={operator.selectedId}
          onSelect={operator.selectPending}
        >
          <QueueFooter
            canClearPending={operator.canClearPending}
            flash={operator.flash}
            onClearPending={operator.clearPendingQueue}
          />
        </PendingQueue>
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
        bibleVersion={operator.bibleVersion}
        autoMode={operator.autoMode}
        onAutoModeChange={operator.setAutoApproveMode}
        appTheme={theme}
        onAppThemeChange={setTheme}
      />
    </div>
  )
}
