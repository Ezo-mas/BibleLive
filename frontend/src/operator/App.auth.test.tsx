import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen } from '@testing-library/react'
import App from './App'

/** The subset of WebSocket the operator app uses, plus the URL it was opened with. */
interface MockSocket {
  url: string
  onopen: (() => void) | null
  onmessage: ((event: { data: string }) => void) | null
  onclose: ((event: { code: number }) => void) | null
  onerror: ((event: unknown) => void) | null
  readyState: number
  send: ReturnType<typeof vi.fn>
  close: ReturnType<typeof vi.fn>
}

const created: MockSocket[] = []

const MockWebSocket = Object.assign(
  function MockWebSocketStub(url: string) {
    const instance: MockSocket = {
      url,
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
  },
  { OPEN: 1, CLOSING: 2, CLOSED: 3 }
)

function latest() {
  return created[created.length - 1]
}

function sent(socket: MockSocket): unknown[] {
  return socket.send.mock.calls.map(([text]) => JSON.parse(String(text)))
}

beforeEach(() => {
  created.length = 0
  window.localStorage.clear()
  vi.stubGlobal('WebSocket', MockWebSocket)
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => new Response(JSON.stringify({}), { status: 503 }))
  )
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

describe('operator socket authentication', () => {
  it('sends the token as the first message, never in the URL', () => {
    window.localStorage.setItem('biblelive_operator_token', 'secret')
    render(<App />)

    const socket = latest()
    expect(socket.url).toMatch(/\/ws\/operator$/)
    expect(socket.url).not.toContain('secret')

    act(() => {
      socket.onopen?.()
    })
    expect(sent(socket)).toEqual([{ action: 'authenticate', token: 'secret' }])
  })

  it('sends the token again on every reconnect', () => {
    window.localStorage.setItem('biblelive_operator_token', 'secret')
    render(<App />)
    act(() => {
      latest().onopen?.()
    })
    act(() => {
      latest().onclose?.({ code: 1006 })
    })

    act(() => {
      screen.getByRole('button', { name: /reconnect/i }).click()
    })
    const replacement = latest()
    act(() => {
      replacement.onopen?.()
    })

    expect(created).toHaveLength(2)
    expect(sent(replacement)).toEqual([{ action: 'authenticate', token: 'secret' }])
  })

  it('sends nothing on open when there is no token', () => {
    render(<App />)
    act(() => {
      latest().onopen?.()
    })

    expect(latest().send).not.toHaveBeenCalled()
  })

  it('opens a new socket with the token the operator enters', () => {
    render(<App />)
    // A server that wants a token closes with 1008 before anything else.
    act(() => {
      latest().onclose?.({ code: 1008 })
    })

    fireEvent.change(screen.getByLabelText('Operator Token'), { target: { value: ' typed ' } })
    fireEvent.click(screen.getByRole('button', { name: 'Connect' }))

    const socket = latest()
    expect(created).toHaveLength(2)
    expect(socket.url).not.toContain('typed')
    act(() => {
      socket.onopen?.()
    })
    expect(sent(socket)).toEqual([{ action: 'authenticate', token: 'typed' }])
  })
})
