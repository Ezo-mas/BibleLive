import { useOperatorState } from './hooks/useOperatorState'
import { useOperatorShortcuts } from './hooks/useOperatorShortcuts'
import { PendingQueue } from './components/PendingQueue'
import { HistoryPanel } from './components/HistoryPanel'
import { DisplaySettings } from './components/DisplaySettings'
import { AutoApproveControls } from './components/AutoApproveControls'
import { StatsPanel } from './components/StatsPanel'
import { OperatorHeader } from './components/OperatorHeader'
import { OperatorActions } from './components/OperatorActions'
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
 * The only path from a pending reference to the display is approving it by ID:
 * the server owns the approval, the display, and history, and the operator
 * console just asks.
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

      <main className="operator-main">
        <PendingQueue
          pending={operator.pending}
          count={operator.pendingCount}
          selectedId={operator.selectedId}
          onSelect={operator.selectPending}
        >
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

        <aside className="operator-sidebar">
          <StatsPanel
            pendingCount={operator.pendingCount}
            shownCount={operator.shownCount}
            historyCount={operator.historyCount}
          />

          <HistoryPanel history={operator.history} count={operator.historyCount} />
        </aside>
      </main>

      <ShortcutOverlay />
    </div>
  )
}
