import { useEffect, useState } from 'react'
import { Button } from '../components'
import { useTheme } from '../hooks/useTheme'
import { useOperatorState } from './hooks/useOperatorState'
import { useOperatorShortcuts } from './hooks/useOperatorShortcuts'
import { LivePanel } from './components/LivePanel'
import { PendingQueue } from './components/PendingQueue'
import { PlanPanel } from './components/PlanPanel'
import { SidePanel } from './components/SidePanel'
import type { SideTab } from './components/SidePanel'
import { FollowAlongHint } from './components/FollowAlongHint'
import { HistoryPanel } from './components/HistoryPanel'
import { ScriptureSearch } from './components/ScriptureSearch'
import { SettingsDialog } from './components/SettingsDialog'
import { OperatorHeader } from './components/OperatorHeader'
import { LanguageStatus } from './components/LanguageStatus'
import { QueueFooter } from './components/QueueFooter'
import { ConnectionBanner } from './components/ConnectionBanner'
import { PipelineBanner } from './components/PipelineBanner'
import { FeedbackNotice } from './components/FeedbackNotice'
import { TokenPrompt } from './components/TokenPrompt'
import { useI18n } from '../shared/i18nContext'
import '../styles/tokens.css'
import '../styles/global.css'
import '../styles/controls.css'
import './styles/base.css'
import './styles/header.css'
import './styles/layout.css'
import './styles/live-panel.css'
import './styles/side-panel.css'
import './styles/settings.css'
import './styles/scripture-search.css'
import './styles/settings-dialog.css'
import './styles/responsive.css'

/**
 * Operator console.
 *
 * Composes the page from the operator state hook and the presentational
 * components; all server state and actions live in `useOperatorState` and all
 * keyboard handling in `useOperatorShortcuts`.
 *
 * The workspace reads like a switcher. On the left, ON DISPLAY is what the audience
 * is looking at, with Clear and the Live / Off air toggle,
 * and, always in view under it, the Plan of passages lined up in advance. On the
 * right is one full-height panel with two tabs: the pending queue of detections
 * (the default) and history. Find Scripture is a command bar in the header, so it
 * is one keystroke away from anywhere.
 *
 * Preferences live behind the header's gear. The queue menu also offers a quick
 * Auto-approve toggle.
 *
 * There is one keyboard cursor, in Pending or Plan: a click (or focus) highlights
 * a row and sends nothing; Enter or the row's Show displays it. A new detection
 * never takes the highlight, and Enter does nothing while the highlighted row's
 * tab is hidden.
 *
 * Scripture reaches the display by one of two doors, and they stay separate all
 * the way down. A pending reference is approved by ID through the server's
 * approval path; a passage the operator finds by hand — or picks from the plan or
 * history — is sent as coordinates for the server to resolve itself. Typing in
 * search never reaches the display by itself: the operator's own Enter shows the
 * resolved passage, and Ctrl+Enter adds it to the plan.
 */
export default function App() {
  const operator = useOperatorState()
  // The console's own light/dark/system setting. Applied here rather than only
  // in the settings dialog's control, so a stored choice is in effect from the
  // first paint instead of waiting for the dialog to be opened.
  const { theme, setTheme } = useTheme()
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [sideTab, setSideTab] = useState<SideTab>('pending')

  const pendingVisible = sideTab === 'pending'
  const { cursor } = operator
  useOperatorShortcuts({
    canShow: cursor.canShow(pendingVisible),
    canClearDisplay: operator.canClearDisplay,
    // The dialog owns the keyboard while it is open (see useOperatorShortcuts).
    enabled: !settingsOpen,
    onNext: () => cursor.move(1, pendingVisible),
    onPrevious: () => cursor.move(-1, pendingVisible),
    onShow: () => cursor.show(pendingVisible),
    onDeselect: cursor.clear,
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
        showLogout={operator.isAuthenticated}
        onLogout={operator.logout}
        onOpenSettings={() => setSettingsOpen(true)}
        language={
          <LanguageStatus
            language={operator.spokenLanguage.language}
            stale={operator.pipeline?.status === 'offline'}
          />
        }
        search={
          <ScriptureSearch
            search={operator.search}
            disabled={!operator.isConnected}
            // Enter in the box shows a passage; not from behind the settings modal.
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
            displayMissing={operator.displayMissing}
            verseLimit={operator.verseLimit}
            displayTheme={operator.displayTheme}
            live={operator.live}
            onLiveChange={operator.setLive}
            liveDisabled={!operator.isConnected}
            clearAction={
              <Button
                type="button"
                variant="subtle"
                size="sm"
                onClick={operator.clearDisplay}
                disabled={!operator.canClearDisplay}
                className={operator.flash === 'clear' ? 'btn-flash' : undefined}
                aria-label={t('stage.clearDisplay')}
                title={t('quick.clearTitle')}
                aria-keyshortcuts="B"
              >
                {t('stage.clear')}
              </Button>
            }
            idleImage={
              operator.clearMode === 'blank' && operator.seenPassage ? null : operator.idleImage
            }
            overlay={
              <FollowAlongHint
                hint={operator.followAlong}
                text={operator.followAlongText}
                canShow={operator.canShowFollowAlong}
                onShow={operator.showFollowAlong}
              />
            }
          />

          <PlanPanel
            items={operator.plan.items}
            snippets={operator.planSnippets}
            currentDisplay={operator.currentDisplay}
            displayConnected={operator.displayConnected}
            live={operator.live}
            selectedId={cursor.planCursorId}
            keyboardMoves={cursor.moves}
            onSelect={id => cursor.highlight('plan', id)}
            canShow={operator.isConnected}
            onShow={operator.showPlanItem}
            onRemove={operator.plan.remove}
            onMove={operator.plan.move}
            onClear={() => {
              if (window.confirm(t('plan.confirmClear'))) operator.plan.clear()
            }}
          />
        </div>

        <SidePanel
          tab={sideTab}
          onTabChange={setSideTab}
          pendingCount={operator.pendingCount}
          autoMode={operator.autoMode}
        >
          {sideTab === 'pending' ? (
            <PendingQueue
              pending={operator.pending}
              selectedId={cursor.pendingCursorId}
              keyboardMoves={cursor.moves}
              onSelect={id => cursor.highlight('pending', id)}
              onShow={operator.approvePending}
              onRemove={operator.discardPending}
              discardInFlight={operator.discardInFlight}
              canShow={operator.isConnected}
            />
          ) : (
            <HistoryPanel
              history={operator.history}
              onShow={operator.showHistory}
              canShow={operator.isConnected}
            />
          )}
          <QueueFooter
            tab={sideTab}
            hasPending={operator.pending.length > 0}
            canClearPending={operator.canClearPending}
            flash={operator.flash === 'clearPending'}
            onClearPending={operator.clearPendingQueue}
            autoMode={operator.autoMode}
            disabled={!operator.isConnected}
            onAutoModeChange={operator.setAutoApproveMode}
          />
        </SidePanel>
      </main>

      <SettingsDialog
        open={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        displayTheme={operator.displayTheme}
        verseLimit={operator.verseLimit}
        onVerseLimitChange={operator.setDisplayVerseLimit}
        disabled={!operator.isConnected}
        onThemeChange={operator.setDisplayBackground}
        clearMode={operator.clearMode}
        onClearModeChange={operator.setDisplayClearMode}
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
