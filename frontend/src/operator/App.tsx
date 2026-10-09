import { useOperatorState } from './hooks/useOperatorState'
import { useOperatorShortcuts } from './hooks/useOperatorShortcuts'
import { LivePanel } from './components/LivePanel'
import { NextPanel } from './components/NextPanel'
import { PendingQueue } from './components/PendingQueue'
import { HistoryPanel } from './components/HistoryPanel'
import { DisplaySettings } from './components/DisplaySettings'
import { AutoApproveControls } from './components/AutoApproveControls'
import { StatsPanel } from './components/StatsPanel'
import { OperatorHeader } from './components/OperatorHeader'
import { OperatorActions } from './components/OperatorActions'
import { ConnectionBanner } from './components/ConnectionBanner'
import { FeedbackNotice } from './components/FeedbackNotice'
import { ShortcutOverlay } from './components/ShortcutOverlay'
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
 * pending queue. Configuration (display settings and auto-approve) and history
 * sit in the sidebar, so the live parts keep the height they need on any window.
 *
 * The only path from a pending reference to the display is approving it by ID:
 * the server owns the approval, the display, and history, and the operator
 * console just asks. Staging a reference in NEXT does not bypass that — Show is
 * the same approval request.
 */
export default function App() {
  const operator = useOperatorState()

  useOperatorShortcuts({
    hasSelection: operator.selectedId !== null,
    onNext: operator.selectNext,
    onPrevious: operator.selectPrevious,
    onApprove: operator.approveSelected,
    onDeselect: operator.deselect,
  })

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

        <aside className="operator-sidebar">
          <StatsPanel
            pendingCount={operator.pendingCount}
            shownCount={operator.shownCount}
            historyCount={operator.historyCount}
          />

          <section className="operator-section operator-settings">
            <div className="operator-section-header">
              <h2 className="operator-section-title">Display &amp; Auto-approve</h2>
            </div>
            <DisplaySettings
              verseLimit={operator.verseLimit}
              displayTheme={operator.displayTheme}
              disabled={!operator.isConnected}
              onVerseLimitChange={operator.setDisplayVerseLimit}
              onThemeChange={operator.setDisplayBackground}
            >
              <AutoApproveControls
                autoMode={operator.autoMode}
                disabled={!operator.isConnected}
                onAutoModeChange={operator.setAutoApproveMode}
              />
            </DisplaySettings>
          </section>

          <HistoryPanel history={operator.history} count={operator.historyCount} />
        </aside>
      </main>
      <FeedbackNotice notice={operator.notice} onDismiss={operator.dismissNotice} />
      <ShortcutOverlay />
    </div>
  )
}
