import { useEffect, useState } from 'react'
import { useTheme } from '../hooks/useTheme'
import { useOperatorState } from './hooks/useOperatorState'
import { useOperatorShortcuts } from './hooks/useOperatorShortcuts'
import { LivePanel } from './components/LivePanel'
import { NextPanel } from './components/NextPanel'
import { PendingQueue } from './components/PendingQueue'
import { PlanPanel } from './components/PlanPanel'
import { SidePanel } from './components/SidePanel'
import type { SideTab } from './components/SidePanel'
import { FollowAlongHint } from './components/FollowAlongHint'
import { HistoryPanel } from './components/HistoryPanel'
import { ScriptureSearch } from './components/ScriptureSearch'
import { DisplayQuickControls } from './components/DisplayQuickControls'
import { SettingsDialog } from './components/SettingsDialog'
import type { SettingsSection } from './components/SettingsDialog'
import { OperatorHeader } from './components/OperatorHeader'
import { LanguageChip } from './components/LanguageChip'
import { QueueFooter } from './components/OperatorActions'
import { ConnectionBanner } from './components/ConnectionBanner'
import { PipelineBanner } from './components/PipelineBanner'
import { FeedbackNotice } from './components/FeedbackNotice'
import { TokenPrompt } from './components/TokenPrompt'
import { useI18n } from '../shared/i18nContext'
import '../styles/tokens.css'
import '../styles/global.css'
import '../styles/controls.css'
import './operator.css'

/**
 * Operator console.
 *
 * Composes the page from the operator state hook and the presentational
 * components; all server state and actions live in `useOperatorState` and all
 * keyboard handling in `useOperatorShortcuts`.
 *
 * The workspace reads like a switcher: ON DISPLAY is what the audience is looking
 * at and UP NEXT is the scripture being prepared for it (and the one filled button,
 * Show on display, that sends it). The column on the right is one panel with three
 * tabs: the pending queue of detections (the default), the plan of passages lined
 * up in advance, and history. Find Scripture is a command bar in the header, so it
 * is one keystroke away from anywhere.
 *
 * Preferences — verses shown, display background, app theme, auto-approve — all
 * live in one settings dialog behind the header's gear. Keeping them out of the
 * page is what gives the stages and the queue their space, and the auto-approve
 * line under ON DISPLAY is the single reminder on screen that the server may be
 * displaying detections on its own.
 *
 * Scripture reaches the display by one of two doors, and they stay separate all
 * the way down. A pending reference is approved by ID through the server's
 * approval path; a passage the operator finds by hand — or picks from the plan or
 * history — is staged in UP NEXT and sent as coordinates for the server to resolve
 * itself. Typing in search never reaches the display by itself: the operator's own
 * Enter shows the resolved passage, Ctrl+Enter stages it, and a double-click on a
 * list row is Show on display without the staging step.
 */
export default function App() {
  const operator = useOperatorState()
  // The console's own light/dark/system setting. Applied here rather than only
  // in the settings dialog's control, so a stored choice is in effect from the
  // first paint instead of waiting for the dialog to be opened.
  const { theme, setTheme } = useTheme()
  const [settingsSection, setSettingsSection] = useState<SettingsSection>('display')
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [sideTab, setSideTab] = useState<SideTab>('pending')

  useOperatorShortcuts({
    hasSelection: operator.selectedId !== null,
    canClearDisplay: operator.canClearDisplay,
    // The dialog owns the keyboard while it is open (see useOperatorShortcuts).
    enabled: !settingsOpen,
    onNext: operator.selectNext,
    onPrevious: operator.selectPrevious,
    onApprove: operator.approveSelected,
    onDeselect: operator.deselect,
    onClearDisplay: operator.clearDisplay,
  })

  // The pipeline can be started, restarted, or switched from a terminal while the
  // console is open, so look again whenever the dialog that shows and changes the
  // language is opened — the reading the operator acts on is then current.
  const { refresh: refreshSpokenLanguage } = operator.spokenLanguage
  const { refresh: refreshBibleVersion } = operator.bibleVersion
  const { refresh: refreshIdleScreen } = operator.idleScreen
  const isConnected = operator.isConnected
  useEffect(() => {
    if (settingsOpen && isConnected) {
      refreshSpokenLanguage()
      refreshBibleVersion()
      refreshIdleScreen()
    }
  }, [settingsOpen, isConnected, refreshSpokenLanguage, refreshBibleVersion, refreshIdleScreen])

  // The launcher's interface language is asked for again on connect.
  const { refreshServerLanguage, t } = useI18n()
  useEffect(() => {
    if (isConnected) refreshServerLanguage()
  }, [isConnected, refreshServerLanguage])

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
            // Enter in the box shows a passage directly; not from behind
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
          <LivePanel
            reference={operator.currentDisplay}
            verseLimit={operator.verseLimit}
            displayTheme={operator.displayTheme}
            idleImage={operator.idleImage}
            overlay={
              <FollowAlongHint
                hint={operator.followAlong}
                text={operator.followAlongText}
                canShow={operator.canShowFollowAlong}
                onShow={operator.showFollowAlong}
              />
            }
          >
            <DisplayQuickControls
              canClearDisplay={operator.canClearDisplay}
              flash={operator.flash}
              onClearDisplay={operator.clearDisplay}
              verseLimit={operator.verseLimit}
              autoMode={operator.autoMode}
              disabled={!operator.isConnected}
              onVerseLimitChange={operator.setDisplayVerseLimit}
              onAutoModeChange={operator.setAutoApproveMode}
            />
          </LivePanel>

          <NextPanel
            reference={operator.nextReference}
            source={operator.nextSource}
            verseLimit={operator.verseLimit}
            canShow={operator.canShowNext}
            flashing={operator.flash === 'approve'}
            onShow={operator.showNext}
            onDiscard={operator.clearNext}
          />
        </div>

        <SidePanel
          tab={sideTab}
          onTabChange={setSideTab}
          counts={{ pending: operator.pendingCount, plan: operator.plan.items.length }}
        >
          {sideTab === 'pending' && (
            <PendingQueue
              pending={operator.pending}
              selectedId={operator.selectedId}
              onSelect={operator.selectPending}
              onShow={operator.approvePending}
              canShow={operator.isConnected}
            >
              <QueueFooter
                canClearPending={operator.canClearPending}
                flash={operator.flash}
                onClearPending={operator.clearPendingQueue}
              />
            </PendingQueue>
          )}
          {sideTab === 'plan' && (
            <PlanPanel
              items={operator.plan.items}
              snippets={operator.planSnippets}
              canShow={operator.isConnected}
              onStage={operator.stagePlanItem}
              onShow={operator.showPlanItem}
              onRemove={operator.plan.remove}
              onMove={operator.plan.move}
              onClear={() => {
                if (window.confirm(t('plan.confirmClear'))) operator.plan.clear()
              }}
            />
          )}
          {sideTab === 'history' && (
            <HistoryPanel
              history={operator.history}
              selectedId={operator.selectedHistoryId}
              onSelect={operator.selectHistory}
              onShow={operator.showHistory}
            />
          )}
        </SidePanel>
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
        idleScreen={operator.idleScreen}
        autoMode={operator.autoMode}
        onAutoModeChange={operator.setAutoApproveMode}
        followAuto={operator.followAuto}
        onFollowAutoChange={operator.setFollowAutoEnabled}
        appTheme={theme}
        onAppThemeChange={setTheme}
      />
    </div>
  )
}
