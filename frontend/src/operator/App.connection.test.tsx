import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, render, screen } from '@testing-library/react'
import App from './App'
import { I18nProvider } from '../shared/I18nProvider'
import { createMockSocket, push } from '../test/operatorHarness'
import type { MockSocket } from '../test/operatorHarness'

/** Every socket the app opened, in order, so a reconnect is observable. */
const created: MockSocket[] = []

const MockWebSocket = Object.assign(
  function MockWebSocketStub() {
    const instance = createMockSocket()
    created.push(instance)
    return instance
  },
  { OPEN: 1, CLOSING: 2, CLOSED: 3 }
)

/** The most recently opened socket. */
function latest() {
  return created[created.length - 1]
}

beforeEach(() => {
  created.length = 0
  window.localStorage.clear()
  // A stored token keeps the console itself on screen: without one the closed
  // socket shows the token prompt instead, which is a different flow.
  window.localStorage.setItem('biblelive_operator_token', 'secret')
  vi.stubGlobal('WebSocket', MockWebSocket)
  // jsdom has no matchMedia; ThemeToggle's useTheme hook reads it on mount.
  vi.stubGlobal('matchMedia', (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }))
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('connection recovery', () => {
  it('treats a drop after connecting as a drop, not an auth failure', () => {
    // This deployment asks for no token at all, so the console must not answer a
    // dropped socket with a token prompt the operator cannot satisfy.
    window.localStorage.clear()
    render(<App />)
    act(() => {
      latest().onopen?.()
    })

    act(() => {
      latest().onclose?.({ code: 1006 })
    })

    expect(screen.queryByLabelText(/operator token/i)).not.toBeInTheDocument()
    expect(screen.getByText(/not connected to the server/i)).toBeInTheDocument()
  })

  it('stays quiet while connected and explains a drop', () => {
    render(<App />)
    act(() => {
      latest().onopen?.()
    })
    expect(screen.queryByText(/not connected to the server/i)).not.toBeInTheDocument()

    act(() => {
      latest().onclose?.({ code: 1006 })
    })

    expect(screen.getByText(/not connected to the server/i)).toBeInTheDocument()
    // Every action is paused while the socket is down.
    expect(screen.getByLabelText('Find scripture by book or reference')).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Live' })).toBeDisabled()
  })

  it('keeps retrying beyond ten attempts with a 30 second cap', () => {
    vi.useFakeTimers()
    render(<App />)
    for (let attempt = 1; attempt <= 12; attempt += 1) {
      act(() => {
        latest().onclose?.({ code: 1006 })
        vi.advanceTimersByTime(Math.min(attempt * 3000, 30_000) - 1)
      })
      expect(created).toHaveLength(attempt)
      act(() => vi.advanceTimersByTime(1))
      expect(created).toHaveLength(attempt + 1)
    }
  })

  it('reconnects immediately on request and resets the backoff', () => {
    vi.useFakeTimers()
    render(<App />)
    for (let attempt = 1; attempt <= 3; attempt += 1) {
      act(() => {
        latest().onclose?.({ code: 1006 })
        vi.advanceTimersByTime(attempt * 3000)
      })
    }
    act(() => latest().onclose?.({ code: 1006 }))
    act(() => screen.getByRole('button', { name: 'Reconnect' }).click())
    expect(created).toHaveLength(5)
    act(() => {
      latest().onclose?.({ code: 1006 })
      vi.advanceTimersByTime(2999)
    })
    expect(created).toHaveLength(5)
    act(() => vi.advanceTimersByTime(1))
    expect(created).toHaveLength(6)
    act(() => vi.advanceTimersByTime(10_000))
    expect(created).toHaveLength(6)
  })

  it('reconnects on request and ignores the replaced socket', () => {
    render(<App />)
    act(() => {
      latest().onopen?.()
    })
    act(() => {
      latest().onclose?.({ code: 1006 })
    })
    const retired = created[0]

    act(() => {
      screen.getByRole('button', { name: 'Reconnect' }).click()
    })

    expect(created).toHaveLength(2)
    expect(screen.queryByText(/not connected to the server/i)).not.toBeInTheDocument()

    // The retired socket's late close must not knock the fresh attempt offline.
    act(() => {
      retired.onclose?.({ code: 1006 })
    })
    expect(screen.queryByText(/not connected to the server/i)).not.toBeInTheDocument()

    act(() => {
      latest().onopen?.()
    })
    expect(screen.queryByText(/not connected to the server/i)).not.toBeInTheDocument()
  })
})

/** A snapshot with this many display pages connected. */
function snapshot(displays: number) {
  push(latest(), {
    type: 'state_snapshot',
    data: {
      current_display: null,
      pending: [],
      history: [],
      pending_count: 0,
      history_count: 0,
      display_connections: displays,
      operator_connections: 1,
      display_settings: { verse_limit: 3, theme: 'dark' },
    },
  })
}

const noDisplay = () => screen.queryByText('No display connected')
const connecting = () => screen.queryByText('Connecting…')

describe('connection status chips', () => {
  it('says nothing in normal operation: no Connected, no Display Active', () => {
    render(<App />)
    act(() => {
      latest().onopen?.()
    })
    snapshot(1)

    expect(noDisplay()).not.toBeInTheDocument()
    expect(connecting()).not.toBeInTheDocument()
    expect(screen.queryByText('Connected')).not.toBeInTheDocument()
    expect(screen.queryByText('Display Active')).not.toBeInTheDocument()
    expect(document.querySelector('.operator-status-chip')).toBeNull()
    // Screen readers still have the connection state.
    expect(screen.getByText('Connection status: Connected')).toHaveClass('sr-only')
  })

  it('warns beside Live while no display page is connected, and stops once one connects', () => {
    render(<App />)
    act(() => {
      latest().onopen?.()
    })
    snapshot(0)

    const live = screen.getByRole('button', { name: 'Live' })
    expect(noDisplay()).toHaveClass('operator-status-chip')
    expect(live.nextElementSibling).toBe(noDisplay())
    expect(live).toHaveAccessibleDescription('No display connected')
    expect(screen.getByRole('banner')).not.toContainElement(noDisplay())

    push(latest(), { type: 'connection_status', data: { display: 1, operator: 1 } })
    expect(noDisplay()).not.toBeInTheDocument()
    expect(live).not.toHaveAttribute('aria-describedby')

    push(latest(), { type: 'connection_status', data: { display: 0, operator: 1 } })
    expect(noDisplay()).toBeInTheDocument()
  })

  it('claims no missing display before the server has said', () => {
    render(<App />)
    act(() => {
      latest().onopen?.()
    })

    expect(noDisplay()).not.toBeInTheDocument()
  })

  it('shows Connecting… while the socket connects, and nothing once it is open', () => {
    render(<App />)

    expect(connecting()).toHaveClass('operator-status-chip')
    expect(screen.getByText('Connection status: Connecting')).toBeInTheDocument()

    act(() => {
      latest().onopen?.()
    })
    snapshot(1)
    expect(connecting()).not.toBeInTheDocument()
  })

  it('leaves a closed socket to its banner: no chip repeats it', () => {
    render(<App />)
    act(() => {
      latest().onopen?.()
    })
    snapshot(0)
    act(() => {
      latest().onclose?.({ code: 1006 })
    })

    expect(screen.getByText(/not connected to the server/i)).toBeInTheDocument()
    expect(connecting()).not.toBeInTheDocument()
    // Whether a display is connected is not known while the socket is down.
    expect(noDisplay()).not.toBeInTheDocument()
    expect(screen.getByText('Connection status: Disconnected')).toBeInTheDocument()

    act(() => {
      screen.getByRole('button', { name: 'Reconnect' }).click()
    })
    expect(connecting()).toBeInTheDocument()
  })

  it('says it in Lithuanian too', () => {
    window.localStorage.setItem('biblelive_ui_language', 'lt')
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(JSON.stringify({ language: null })))
    )
    render(
      <I18nProvider>
        <App />
      </I18nProvider>
    )
    expect(screen.getByText('Jungiamasi…')).toBeInTheDocument()
    act(() => {
      latest().onopen?.()
    })
    snapshot(0)

    expect(screen.getByText('Ekranas neprijungtas')).toBeInTheDocument()
  })
})
