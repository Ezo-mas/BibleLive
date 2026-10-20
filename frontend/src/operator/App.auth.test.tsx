import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen } from '@testing-library/react'
import App from './App'
import { I18nProvider } from '../shared/I18nProvider'

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

  it('sends an empty authenticate when there is no token', () => {
    render(<App />)
    act(() => {
      latest().onopen?.()
    })

    // The empty message draws the server's refusal at once instead of waiting
    // out its first-message timeout; a server without auth ignores it.
    expect(sent(latest())).toEqual([{ action: 'authenticate', token: '' }])
  })

  it('opens the token prompt at once, without the failure text, when no token was tried', () => {
    render(<App />)
    act(() => {
      latest().onopen?.()
    })
    // An auth-on server answers the empty first message with 1008 right away.
    act(() => {
      latest().onclose?.({ code: 1008 })
    })

    expect(screen.getByLabelText('Operator Token')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('shows the failure text when the server rejects a saved token', () => {
    window.localStorage.setItem('biblelive_operator_token', 'wrong')
    render(<App />)
    act(() => {
      latest().onopen?.()
    })
    act(() => {
      latest().onclose?.({ code: 1008 })
    })

    expect(screen.getByLabelText('Operator Token')).toBeInTheDocument()
    expect(screen.getByRole('alert')).toHaveTextContent(/authentication failed/i)
    expect(window.localStorage.getItem('biblelive_operator_token')).toBeNull()
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

describe('operator token in the launcher link (#token=)', () => {
  afterEach(() => {
    window.history.replaceState(null, '', '/')
  })

  it('takes the token from the fragment, removes it from the address, and sends it first', () => {
    window.history.replaceState(null, '', '/operator#token=secret')
    render(<App />)

    expect(window.location.hash).toBe('')
    expect(window.location.pathname).toBe('/operator')
    expect(latest().url).not.toContain('secret')
    act(() => {
      latest().onopen?.()
    })

    expect(sent(latest())).toEqual([{ action: 'authenticate', token: 'secret' }])
    expect(screen.queryByLabelText('Operator Token')).not.toBeInTheDocument()
    expect(window.localStorage.getItem('biblelive_operator_token')).toBe('secret')
  })

  it('asks the server for the interface language with the fragment token', () => {
    const fetchMock = vi.fn(
      async () => new Response(JSON.stringify({ language: 'en' }), { status: 200 })
    )
    vi.stubGlobal('fetch', fetchMock)
    window.history.replaceState(null, '', '/operator#token=secret')
    render(
      <I18nProvider>
        <App />
      </I18nProvider>
    )

    expect(fetchMock).toHaveBeenCalledWith('/api/ui-language', {
      headers: { Authorization: 'Bearer secret' },
    })
  })

  it('replaces a stored token with the fragment token, as a typed one would', () => {
    window.localStorage.setItem('biblelive_operator_token', 'old')
    window.history.replaceState(null, '', '/operator#token=new')
    render(<App />)
    act(() => {
      latest().onopen?.()
    })

    expect(sent(latest())).toEqual([{ action: 'authenticate', token: 'new' }])
    expect(window.localStorage.getItem('biblelive_operator_token')).toBe('new')
  })

  it('still uses the fragment token when the browser refuses to store it', () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('storage is blocked', 'SecurityError')
    })
    window.history.replaceState(null, '', '/operator#token=secret')
    render(<App />)
    act(() => {
      latest().onopen?.()
    })

    expect(sent(latest())).toEqual([{ action: 'authenticate', token: 'secret' }])
    expect(screen.queryByLabelText('Operator Token')).not.toBeInTheDocument()
  })

  it('opens the token prompt with the failure text when the server refuses the fragment token', () => {
    window.history.replaceState(null, '', '/operator#token=wrong')
    render(<App />)
    act(() => {
      latest().onopen?.()
    })
    act(() => {
      latest().onclose?.({ code: 1008 })
    })

    expect(window.location.hash).toBe('')
    expect(screen.getByLabelText('Operator Token')).toBeInTheDocument()
    expect(screen.getByRole('alert')).toHaveTextContent(/authentication failed/i)
    expect(window.localStorage.getItem('biblelive_operator_token')).toBeNull()
  })
})
