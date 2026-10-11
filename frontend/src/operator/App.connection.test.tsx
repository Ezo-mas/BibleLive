import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, render, screen } from '@testing-library/react'
import App from './App'

/** The subset of WebSocket the operator app uses. */
interface MockSocket {
  onopen: (() => void) | null
  onmessage: ((event: { data: string }) => void) | null
  onclose: ((event: { code: number }) => void) | null
  onerror: ((event: unknown) => void) | null
  readyState: number
  send: ReturnType<typeof vi.fn>
  close: ReturnType<typeof vi.fn>
}

/** Every socket the app opened, in order, so a reconnect is observable. */
const created: MockSocket[] = []

function createMockSocket(): MockSocket {
  const instance: MockSocket = {
    onopen: null,
    onmessage: null,
    onclose: null,
    onerror: null,
    readyState: 1,
    send: vi.fn(),
    close: vi.fn(),
  }
  created.push(instance)
  return instance
}

const MockWebSocket = Object.assign(
  function MockWebSocketStub() {
    return createMockSocket()
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
    expect(screen.getByRole('button', { name: /approve and show/i })).toBeDisabled()
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
