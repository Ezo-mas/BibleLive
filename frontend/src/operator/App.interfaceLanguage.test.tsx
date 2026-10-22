import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import App from './App'
import { I18nProvider } from '../shared/I18nProvider'
import { LANGUAGE_STORAGE_KEY } from '../shared/i18n'
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

/** What the launcher reports for the console's language; null means it has no setting. */
let launcherLanguage: string | null = null

/** The endpoints the console reads on connect; anything unlisted gets an empty answer. */
function fakeFetch(input: RequestInfo | URL) {
  const url = String(input)
  if (url.startsWith('/api/ui-language'))
    return Promise.resolve(jsonResponse({ language: launcherLanguage }))
  if (url.startsWith('/api/language')) return Promise.resolve(jsonResponse({ language: 'en' }))
  if (url.startsWith('/api/bible-version')) {
    return Promise.resolve(jsonResponse({ version: 'kjv', available: ['kjv', 'ltb'] }))
  }
  if (url.startsWith('/api/pipeline/')) {
    return Promise.resolve(
      jsonResponse({ status: 'unknown', language: null, problems: [], seconds_since_report: null })
    )
  }
  return Promise.resolve(jsonResponse({ books: [] }))
}

/** Render the console in the provider the app uses, connect its socket, and let the first answers land. */
async function renderConnected() {
  render(
    <I18nProvider>
      <App />
    </I18nProvider>
  )
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
      auto_mode: 'off',
    },
  })
  await act(async () => {
    await Promise.resolve()
  })
}

/** The header's gear, named in whichever language the console is showing. */
function gear() {
  return screen.getByRole('button', { name: /^(Settings|Nustatymai)$/ })
}

/** The settings dialog's interface-language group, the only place the choice is made. */
function interfaceChoices() {
  const dialog = screen.getByRole('dialog')
  return within(within(dialog).getByRole('group', { name: /^(Interface language|Sąsajos kalba)$/ }))
}

beforeEach(() => {
  window.localStorage.clear()
  launcherLanguage = null
  vi.stubGlobal('WebSocket', MockWebSocket)
  vi.stubGlobal('fetch', vi.fn(fakeFetch))
  // jsdom has no matchMedia; the theme control reads it when the dialog mounts.
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
  window.localStorage.clear()
  socket = null
})

describe('console language', () => {
  it('switches the whole console to Lithuanian from Settings, and remembers the choice', async () => {
    await renderConnected()
    expect(screen.getByRole('heading', { name: 'On Display' })).toBeInTheDocument()

    fireEvent.click(gear())
    fireEvent.click(interfaceChoices().getByRole('button', { name: 'Lietuvių' }))

    expect(await screen.findByRole('heading', { name: 'Ekrane' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Toliau' })).toBeInTheDocument()
    // The sidebar's tabs are named in Lithuanian too (a count may follow the name).
    expect(screen.getByRole('tab', { name: /^Laukiančios/ })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: /^Planas/ })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'Istorija' })).toBeInTheDocument()
    // The settings dialog itself is in Lithuanian too.
    expect(screen.getByRole('dialog', { name: 'Nustatymai' })).toBeInTheDocument()
    expect(window.localStorage.getItem(LANGUAGE_STORAGE_KEY)).toBe('lt')
  })

  it('uses the launcher setting when the operator has not chosen', async () => {
    launcherLanguage = 'lt'
    await renderConnected()
    expect(await screen.findByRole('heading', { name: 'Ekrane' })).toBeInTheDocument()
    expect(window.localStorage.getItem(LANGUAGE_STORAGE_KEY)).toBeNull()
  })

  it('keeps English when the operator chose it, even if the launcher says Lithuanian', async () => {
    launcherLanguage = 'lt'
    window.localStorage.setItem(LANGUAGE_STORAGE_KEY, 'en')
    await renderConnected()
    await waitFor(() => expect(fetch).toHaveBeenCalledWith('/api/ui-language', expect.anything()))
    expect(screen.getByRole('heading', { name: 'On Display' })).toBeInTheDocument()
  })

  it('Auto clears the operator choice, so the launcher setting applies again', async () => {
    launcherLanguage = 'lt'
    await renderConnected()
    expect(await screen.findByRole('heading', { name: 'Ekrane' })).toBeInTheDocument()

    fireEvent.click(gear())
    fireEvent.click(interfaceChoices().getByRole('button', { name: 'English' }))
    expect(await screen.findByRole('heading', { name: 'On Display' })).toBeInTheDocument()
    expect(window.localStorage.getItem(LANGUAGE_STORAGE_KEY)).toBe('en')

    fireEvent.click(interfaceChoices().getByRole('button', { name: 'Auto' }))
    expect(await screen.findByRole('heading', { name: 'Ekrane' })).toBeInTheDocument()
    expect(window.localStorage.getItem(LANGUAGE_STORAGE_KEY)).toBeNull()
  })
})
