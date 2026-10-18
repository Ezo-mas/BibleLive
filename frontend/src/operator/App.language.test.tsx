import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Mock } from 'vitest'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import App from './App'
import type { OperatorMessage } from '../shared/types'

/** The subset of WebSocket the operator app uses. */
interface MockSocket {
  onopen: (() => void) | null
  onmessage: ((event: { data: string }) => void) | null
  onclose: ((event: { code: number }) => void) | null
  onerror: ((event: unknown) => void) | null
  readyState: number
  send: ReturnType<typeof vi.fn>
  close: () => void
}

let socket: MockSocket | null = null

const MockWebSocket = Object.assign(
  function MockWebSocketStub() {
    const instance: MockSocket = {
      onopen: null,
      onmessage: null,
      onclose: null,
      onerror: null,
      readyState: 1,
      send: vi.fn(),
      close: vi.fn(),
    }
    socket = instance
    return instance
  },
  { OPEN: 1, CLOSING: 2, CLOSED: 3 }
)

function push(message: OperatorMessage) {
  act(() => {
    socket?.onmessage?.({ data: JSON.stringify(message) })
  })
}

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as unknown as Response
}

/**
 * The server's `/api/language`, as the console sees it.
 *
 * `language` is what the pipeline reports. `fail` makes the next request answer
 * with an error status; `hold` makes the next switch wait until released, so a
 * test can look at the console while the pipeline has not yet answered.
 */
let language = 'en'
let fail: { status: number; detail: string } | null = null
let release: (() => void) | null = null
let hold = false
let fetchMock: Mock<[input: RequestInfo | URL, init?: RequestInit], Promise<Response>>

function stubFetch() {
  language = 'en'
  fail = null
  release = null
  hold = false
  fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    // The console also polls the pipeline's health once connected; that has its
    // own tests (App.pipeline.test.tsx) and is answered as "unknown" here.
    if (String(input).startsWith('/api/pipeline/')) {
      return jsonResponse({
        status: 'unknown',
        language: null,
        problems: [],
        seconds_since_report: null,
      })
    }
    // The launcher's interface language is read on connect; this file is about the
    // spoken language, so the launcher has no setting here.
    if (String(input).startsWith('/api/ui-language')) {
      return jsonResponse({ language: null })
    }
    expect(String(input)).toBe('/api/language')
    if (fail) {
      const { status, detail } = fail
      return jsonResponse({ detail }, status)
    }
    if (init?.method === 'POST') {
      const requested = (JSON.parse(String(init.body)) as { language: string }).language
      if (hold) {
        await new Promise<void>(resolve => {
          release = resolve
        })
      }
      language = requested
    }
    return jsonResponse({ language })
  })
  vi.stubGlobal('fetch', fetchMock)
}

function openWith() {
  render(<App />)
  act(() => {
    socket?.onopen?.()
  })
  push({
    type: 'state_snapshot',
    data: {
      current_display: null,
      pending: [],
      history: [],
      pending_count: 0,
      history_count: 0,
      display_connections: 1,
      operator_connections: 1,
      display_settings: { verse_limit: 3, theme: 'dark' },
    },
  })
}

function openSettings() {
  act(() => {
    screen.getByRole('button', { name: 'Settings' }).click()
  })
}

/** The language control inside the open settings dialog. */
function languageGroup() {
  return within(screen.getByRole('dialog')).getByRole('group', { name: 'Spoken language' })
}

function languageButton(name: string) {
  return within(languageGroup()).getByRole('button', { name })
}

/** Calls the console made to the language endpoint, by HTTP method. */
function calls(method: 'GET' | 'POST') {
  return fetchMock.mock.calls.filter(
    ([url, init]) => String(url) === '/api/language' && (init?.method ?? 'GET') === method
  )
}

beforeEach(() => {
  vi.stubGlobal('WebSocket', MockWebSocket)
  vi.stubGlobal('confirm', vi.fn())
  // jsdom has no matchMedia; the theme hook reads it as soon as the app mounts.
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
  localStorage.clear()
  stubFetch()
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  localStorage.clear()
  socket = null
})

describe('the spoken language in the header', () => {
  it('reads the language once the console connects and shows it', async () => {
    language = 'lt'
    openWith()

    const chip = await screen.findByRole('button', {
      name: 'Spoken language: Lithuanian. Change in settings',
    })
    expect(chip).toHaveTextContent('LT')
    expect(calls('GET')).toHaveLength(1)
  })

  it('shows nothing, and asks for nothing, before the socket is up', () => {
    render(<App />)

    expect(fetchMock).not.toHaveBeenCalled()
    expect(screen.queryByRole('button', { name: /^Spoken language/ })).not.toBeInTheDocument()
  })

  it('shows no language when the pipeline cannot be reached', async () => {
    fail = { status: 503, detail: 'The audio pipeline is not reachable.' }
    openWith()

    await waitFor(() => expect(fetchMock).toHaveBeenCalled())
    // Nothing is assumed: an unknown language is not drawn as English.
    expect(screen.queryByRole('button', { name: /^Spoken language/ })).not.toBeInTheDocument()
  })

  it('opens Settings on the language control when clicked', async () => {
    openWith()

    const chip = await screen.findByRole('button', { name: /^Spoken language: English/ })
    act(() => {
      chip.click()
    })

    expect(screen.getByRole('dialog')).toBeInTheDocument()
    await waitFor(() => expect(languageButton('English')).toHaveFocus())
  })

  it('sends the operator token with the request', async () => {
    localStorage.setItem('biblelive_operator_token', 'secret-token')
    openWith()

    await screen.findByRole('button', { name: /^Spoken language/ })
    expect(calls('GET')[0][1]?.headers).toEqual({ Authorization: 'Bearer secret-token' })
  })
})

describe('switching the spoken language', () => {
  it('shows both languages with the current one pressed', async () => {
    language = 'lt'
    openWith()
    await screen.findByRole('button', { name: /^Spoken language/ })

    openSettings()

    await waitFor(() =>
      expect(languageButton('Lithuanian')).toHaveAttribute('aria-pressed', 'true')
    )
    expect(languageButton('English')).toHaveAttribute('aria-pressed', 'false')
  })

  it('switches only when the pipeline confirms, and then says so', async () => {
    hold = true
    openWith()
    await screen.findByRole('button', { name: /^Spoken language: English/ })
    openSettings()
    await waitFor(() => expect(languageButton('Lithuanian')).toBeEnabled())

    act(() => {
      languageButton('Lithuanian').click()
    })

    // The request is on the wire; nothing has been confirmed, so nothing changes.
    await waitFor(() => expect(calls('POST')).toHaveLength(1))
    expect(JSON.parse(String(calls('POST')[0][1]?.body))).toEqual({ language: 'lt' })
    expect(languageButton('English')).toHaveAttribute('aria-pressed', 'true')
    expect(languageButton('Lithuanian')).toHaveAttribute('aria-pressed', 'false')
    expect(languageButton('English')).toBeDisabled()
    expect(languageButton('Lithuanian')).toBeDisabled()
    expect(within(screen.getByRole('dialog')).getByText('Switching…')).toBeInTheDocument()

    await act(async () => {
      release?.()
    })

    await waitFor(() =>
      expect(languageButton('Lithuanian')).toHaveAttribute('aria-pressed', 'true')
    )
    expect(languageButton('English')).toBeEnabled()
    expect(screen.getByRole('status')).toHaveTextContent('Spoken language: Lithuanian')
    expect(
      screen.getByRole('button', { name: 'Spoken language: Lithuanian. Change in settings' })
    ).toHaveTextContent('LT')
  })

  it('does not send a request for the language that is already active', async () => {
    openWith()
    await screen.findByRole('button', { name: /^Spoken language/ })
    openSettings()
    await waitFor(() => expect(languageButton('English')).toBeEnabled())

    act(() => {
      languageButton('English').click()
    })

    expect(calls('POST')).toHaveLength(0)
  })

  it('keeps the old language and shows the reason when the pipeline refuses', async () => {
    openWith()
    await screen.findByRole('button', { name: /^Spoken language: English/ })
    openSettings()
    await waitFor(() => expect(languageButton('Lithuanian')).toBeEnabled())
    fail = { status: 400, detail: "Unsupported language 'lt'" }

    act(() => {
      languageButton('Lithuanian').click()
    })

    const dialog = within(screen.getByRole('dialog'))
    await dialog.findByText("Unsupported language 'lt'")
    expect(languageButton('English')).toHaveAttribute('aria-pressed', 'true')
    // A refusal is not an outage: the controls stay available for another try.
    expect(languageButton('Lithuanian')).toBeEnabled()
    // Announced once: by the console's notice, not again by the line in the dialog.
    expect(screen.getByRole('alert')).toHaveTextContent("Unsupported language 'lt'")
  })

  it('turns the controls off and offers another look when the pipeline stops answering', async () => {
    openWith()
    await screen.findByRole('button', { name: /^Spoken language: English/ })
    openSettings()
    await waitFor(() => expect(languageButton('Lithuanian')).toBeEnabled())
    fail = { status: 503, detail: 'The audio pipeline is not reachable.' }

    act(() => {
      languageButton('Lithuanian').click()
    })

    const dialog = within(screen.getByRole('dialog'))
    await dialog.findByRole('button', { name: 'Check again' })
    expect(languageButton('English')).toBeDisabled()
    expect(languageButton('Lithuanian')).toBeDisabled()

    // The pipeline comes back; asking again recovers the control.
    fail = null
    act(() => {
      dialog.getByRole('button', { name: 'Check again' }).click()
    })
    await waitFor(() => expect(languageButton('Lithuanian')).toBeEnabled())
    expect(dialog.queryByRole('button', { name: 'Check again' })).not.toBeInTheDocument()
  })
})

describe('reading the language again', () => {
  it('picks up a switch made outside the console when Settings opens', async () => {
    openWith()
    await screen.findByRole('button', { name: /^Spoken language: English/ })

    // Someone ran the pipeline's curl command.
    language = 'lt'
    openSettings()

    await waitFor(() =>
      expect(languageButton('Lithuanian')).toHaveAttribute('aria-pressed', 'true')
    )
    expect(
      screen.getByRole('button', { name: 'Spoken language: Lithuanian. Change in settings' })
    ).toBeInTheDocument()
  })

  it('shows what is wrong, with a way to retry, when the first read failed', async () => {
    fail = { status: 503, detail: 'The audio pipeline is not reachable.' }
    openWith()
    await waitFor(() => expect(fetchMock).toHaveBeenCalled())

    openSettings()

    const dialog = within(screen.getByRole('dialog'))
    expect(await dialog.findByText('The audio pipeline is not reachable.')).toBeInTheDocument()
    expect(languageButton('English')).toBeDisabled()
    expect(languageButton('Lithuanian')).toBeDisabled()

    fail = null
    act(() => {
      dialog.getByRole('button', { name: 'Check again' }).click()
    })
    await waitFor(() => expect(languageButton('English')).toHaveAttribute('aria-pressed', 'true'))
    expect(languageButton('English')).toBeEnabled()
  })

  it('leaves the controls off while the console is disconnected', async () => {
    openWith()
    await screen.findByRole('button', { name: /^Spoken language: English/ })
    openSettings()
    await waitFor(() => expect(languageButton('Lithuanian')).toBeEnabled())

    act(() => {
      socket?.onclose?.({ code: 1006 })
    })

    await waitFor(() => expect(languageButton('Lithuanian')).toBeDisabled())
    expect(languageButton('English')).toBeDisabled()
  })
})
