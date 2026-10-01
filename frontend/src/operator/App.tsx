import { useCallback, useEffect, useRef, useState } from 'react'
import { useWebSocket } from '../shared/useWebSocket'
import { formatReference } from '../shared/reference'
import type {
  BibleReference,
  OperatorAction,
  OperatorMessage,
} from '../shared/types'
import {
  Button,
  StatusBadge,
  EmptyState,
  ConnectionIndicator,
  ThemeToggle,
  BookIcon,
  ClockIcon,
  CheckIcon,
  XIcon,
} from '../components'
import type { ConnectionState } from '../components'
import '../styles/tokens.css'
import '../styles/global.css'
import './operator.css'

/** How long the button "pressed" feedback lasts after a confirmation. */
const FLASH_MS = 150

/** Local storage key for operator token */
const TOKEN_STORAGE_KEY = 'biblelive_operator_token'

type FlashTarget = 'approve' | 'clear' | 'clearPending'

interface ReferenceCardProps {
  reference: BibleReference
  selected?: boolean
  onSelect?: () => void
}

function ReferenceCard({ reference, selected = false, onSelect }: ReferenceCardProps) {
  const selectable = onSelect !== undefined
  
  const confidenceVariant = 
    reference.confidence === 'high' ? 'success' :
    reference.confidence === 'medium' ? 'warning' :
    'danger'

  return (
    <div
      className={`ref-card ${selected ? 'ref-card-selected' : ''}`}
      role={selectable ? 'button' : undefined}
      tabIndex={selectable ? 0 : undefined}
      aria-pressed={selectable ? selected : undefined}
      onClick={onSelect}
      onKeyDown={
        selectable
          ? (e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault()
                onSelect?.()
              }
            }
          : undefined
      }
    >
      {selected && (
        <div className="ref-card-check" aria-hidden="true">
          <CheckIcon />
        </div>
      )}
      
      <div className="ref-card-content">
        <div className="ref-card-header">
          <h3 className="ref-card-title">{formatReference(reference)}</h3>
          <div className="ref-card-badges">
            <StatusBadge variant={confidenceVariant}>
              {reference.confidence}
            </StatusBadge>
            {reference.inferred && (
              <StatusBadge variant="info">Context</StatusBadge>
            )}
          </div>
        </div>
        
        {reference.text && (
          <p className="ref-card-text">{reference.text}</p>
        )}
      </div>
    </div>
  )
}

export default function App() {
  const [token, setToken] = useState<string | null>(() => {
    // Load token from localStorage on mount
    return localStorage.getItem(TOKEN_STORAGE_KEY)
  })
  const [tokenInput, setTokenInput] = useState('')
  const [authError, setAuthError] = useState(false)
  
  const [pending, setPending] = useState<BibleReference[]>([])
  const [history, setHistory] = useState<BibleReference[]>([])
  const [pendingCount, setPendingCount] = useState(0)
  const [historyCount, setHistoryCount] = useState(0)
  const [shownCount, setShownCount] = useState(0)
  const [displayHasContent, setDisplayHasContent] = useState(false)
  const [selectedIndex, setSelectedIndex] = useState<number | null>(null)
  const [flash, setFlash] = useState<FlashTarget | null>(null)
  const [displayConnected, setDisplayConnected] = useState(false)
  const flashTimerRef = useRef<number | undefined>(undefined)

  // Brief "pressed" pulse on a button once the server confirms its action.
  const pulse = (target: FlashTarget) => {
    setFlash(target)
    window.clearTimeout(flashTimerRef.current)
    flashTimerRef.current = window.setTimeout(() => setFlash(null), FLASH_MS)
  }

  useEffect(
    () => () => window.clearTimeout(flashTimerRef.current),
    [],
  )

  const handleMessage = (message: OperatorMessage) => {
    switch (message.type) {
      case 'state_snapshot':
        setPending(message.data.pending)
        setHistory(message.data.history)
        setPendingCount(message.data.pending_count)
        setHistoryCount(message.data.history_count)
        setShownCount(message.data.history_count)
        setDisplayHasContent(message.data.current_display !== null)
        setDisplayConnected(message.data.display_connections > 0)
        setSelectedIndex(null)
        break
      case 'pending_update':
        setPending(message.data.pending)
        setPendingCount(message.data.count)
        setSelectedIndex(null)
        break
      case 'approval_confirmed':
        setDisplayHasContent(true)
        pulse('approve')
        break
      case 'clear_confirmed':
        setDisplayHasContent(false)
        pulse('clear')
        break
      case 'pending_cleared':
        pulse('clearPending')
        break
      case 'connection_status':
        setDisplayConnected(message.data.display > 0)
        break
      case 'error':
        console.error('Server error:', message.data.message)
        break
    }
  }

  const { status, send } = useWebSocket<OperatorMessage>(
    token ? `/ws/operator?token=${encodeURIComponent(token)}` : '/ws/operator',
    handleMessage,
    {
      onClose: (event) => {
        // WebSocket close code 1008 = policy violation (auth failure)
        if (event.code === 1008) {
          setAuthError(true)
          // Clear invalid token
          localStorage.removeItem(TOKEN_STORAGE_KEY)
          setToken(null)
        }
      }
    }
  )

  const handleTokenSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    const trimmed = tokenInput.trim()
    if (trimmed) {
      localStorage.setItem(TOKEN_STORAGE_KEY, trimmed)
      setToken(trimmed)
      setTokenInput('')
      setAuthError(false)
    }
  }

  const handleLogout = () => {
    localStorage.removeItem(TOKEN_STORAGE_KEY)
    setToken(null)
    setAuthError(false)
  }

  const sendAction = useCallback(
    (action: OperatorAction) => {
      send(action)
    },
    [send]
  )

  const approve = useCallback(() => {
    if (selectedIndex !== null && selectedIndex < pending.length) {
      const ref = pending[selectedIndex]
      sendAction({ action: 'approve', reference_id: ref.id })
    }
  }, [selectedIndex, pending, sendAction])

  const clearDisplay = () => {
    sendAction({ action: 'clear' })
  }

  const clearPendingQueue = () => {
    if (window.confirm('Clear all pending references?')) {
      sendAction({ action: 'clear_pending' })
    }
  }

  // Keyboard shortcuts
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      // Only handle shortcuts when not focused on an input
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) {
        return
      }

      switch (e.key) {
        case 'ArrowDown':
          e.preventDefault()
          setSelectedIndex((prev) => {
            if (pending.length === 0) return null
            if (prev === null) return 0
            return Math.min(prev + 1, pending.length - 1)
          })
          break
        case 'ArrowUp':
          e.preventDefault()
          setSelectedIndex((prev) => {
            if (pending.length === 0) return null
            if (prev === null) return 0
            return Math.max(prev - 1, 0)
          })
          break
        case 'Enter':
          if (selectedIndex !== null) {
            e.preventDefault()
            approve()
          }
          break
        case 'Escape':
          e.preventDefault()
          setSelectedIndex(null)
          break
      }
    }

    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [selectedIndex, pending.length, approve])

  const canApprove = selectedIndex !== null && status === 'open'
  const canClear = displayHasContent && status === 'open'
  const canClearPending = pending.length > 0 && status === 'open'

  const wsState: ConnectionState =
    status === 'open'
      ? 'connected'
      : status === 'connecting'
      ? 'connecting'
      : 'disconnected'

  // Show token prompt if auth error or no token when needed
  if (authError || (status === 'closed' && !token)) {
    return (
      <div className="operator-app">
        <div className="operator-auth-container">
          <div className="operator-auth-card">
            <h1 className="operator-auth-title">BibleLive Operator</h1>
            {authError && (
              <div className="operator-auth-error">
                Authentication failed. Please enter your operator token.
              </div>
            )}
            <form onSubmit={handleTokenSubmit} className="operator-auth-form">
              <label htmlFor="token-input" className="operator-auth-label">
                Operator Token
              </label>
              <input
                id="token-input"
                type="password"
                value={tokenInput}
                onChange={(e) => setTokenInput(e.target.value)}
                placeholder="Enter your operator token"
                className="operator-auth-input"
                autoFocus
              />
              <button type="submit" className="operator-auth-button">
                Connect
              </button>
            </form>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="operator-app">
      {/* Header */}
      <header className="operator-header">
        <div className="operator-header-left">
          <h1 className="operator-title">BibleLive</h1>
          <span className="operator-subtitle">Operator Console</span>
        </div>
        
        <div className="operator-header-right">
          <div className="operator-status-group">
            <ConnectionIndicator state={wsState} />
            {displayConnected && (
              <div className="operator-display-status">
                <span className="operator-display-dot" />
                <span className="operator-display-label">Display Active</span>
              </div>
            )}
          </div>
          {token && (
            <button
              onClick={handleLogout}
              className="operator-logout-button"
              title="Logout"
            >
              Logout
            </button>
          )}
          <ThemeToggle />
        </div>
      </header>

      {/* Main Content */}
      <main className="operator-main">
        {/* Pending Queue - Primary Section */}
        <section className="operator-section operator-pending">
          <div className="operator-section-header">
            <h2 className="operator-section-title">Pending References</h2>
            <span className="operator-section-count">{pendingCount}</span>
          </div>
          
          <div className="operator-section-content">
            {pending.length === 0 ? (
              <EmptyState
                icon={<BookIcon />}
                title="No pending references"
                description="Bible references will appear here when detected by the audio pipeline"
              />
            ) : (
              <div className="ref-card-list">
                {pending.map((reference, index) => (
                  <ReferenceCard
                    key={reference.id}
                    reference={reference}
                    selected={index === selectedIndex}
                    onSelect={() => setSelectedIndex(index)}
                  />
                ))}
              </div>
            )}
          </div>

          {/* Actions */}
          <div className="operator-actions">
            <Button
              variant="primary"
              size="lg"
              fullWidth
              icon={<CheckIcon />}
              onClick={approve}
              disabled={!canApprove}
              className={flash === 'approve' ? 'btn-flash' : ''}
            >
              Approve and Show
            </Button>
            
            <div className="operator-actions-secondary">
              <Button
                variant="danger"
                size="md"
                icon={<XIcon />}
                onClick={clearDisplay}
                disabled={!canClear}
                className={flash === 'clear' ? 'btn-flash' : ''}
              >
                Clear Display
              </Button>
              
              <Button
                variant="secondary"
                size="md"
                icon={<XIcon />}
                onClick={clearPendingQueue}
                disabled={!canClearPending}
                className={flash === 'clearPending' ? 'btn-flash' : ''}
              >
                Clear Queue
              </Button>
            </div>
          </div>
        </section>

        {/* Sidebar */}
        <aside className="operator-sidebar">
          {/* Stats */}
          <section className="operator-section operator-stats">
            <h2 className="operator-section-title">Statistics</h2>
            <div className="operator-stats-grid">
              <div className="operator-stat">
                <span className="operator-stat-label">Pending</span>
                <span className="operator-stat-value">{pendingCount}</span>
              </div>
              <div className="operator-stat">
                <span className="operator-stat-label">Shown</span>
                <span className="operator-stat-value">{shownCount}</span>
              </div>
              <div className="operator-stat">
                <span className="operator-stat-label">History</span>
                <span className="operator-stat-value">{historyCount}</span>
              </div>
            </div>
          </section>

          {/* History */}
          <section className="operator-section operator-history">
            <div className="operator-section-header">
              <h2 className="operator-section-title">Recent History</h2>
              <span className="operator-section-count">{historyCount}</span>
            </div>
            
            <div className="operator-section-content">
              {history.length === 0 ? (
                <EmptyState
                  icon={<ClockIcon />}
                  title="No history yet"
                  description="Approved references will appear here"
                />
              ) : (
                <div className="ref-card-list">
                  {history.map((reference) => (
                    <ReferenceCard key={reference.id} reference={reference} />
                  ))}
                </div>
              )}
            </div>
          </section>
        </aside>
      </main>

      {/* Keyboard Shortcuts Help */}
      <div className="operator-shortcuts">
        <span className="operator-shortcuts-label">Shortcuts:</span>
        <kbd>↑↓</kbd> Navigate
        <kbd>Enter</kbd> Approve
        <kbd>Esc</kbd> Clear Selection
      </div>
    </div>
  )
}

