import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import App from './App'
import type { DisplaySettings, OperatorMessage } from '../shared/types'

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
  socket = instance
  return instance
}

/** Constructor stub; `OPEN` is what the shared hook compares readyState to. */
const MockWebSocket = Object.assign(
  function MockWebSocketStub() {
    return createMockSocket()
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

/** Only the idle-screen route answers; the other console reads may fail quietly. */
const fetchMock = vi.fn(async (url: RequestInfo | URL) => {
  if (String(url) === '/api/idle-screen') return jsonResponse({ fit: 'logo', image: null })
  return jsonResponse({ detail: 'not stubbed' }, 404)
})

beforeEach(() => {
  window.localStorage.clear()
  vi.stubGlobal('WebSocket', MockWebSocket)
  vi.stubGlobal('fetch', fetchMock)
  // jsdom has no matchMedia; the theme hook reads it when the dialog mounts.
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
  vi.clearAllMocks()
  socket = null
})

function openConsole(displaySettings: DisplaySettings = { verse_limit: 3, theme: 'dark' }) {
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
      display_settings: displaySettings,
      auto_mode: 'off',
    },
  })
}

/** Let the mocked reads finish and the console settle: its controls wait for each answer. */
async function settle() {
  await act(async () => {
    await new Promise(resolve => setTimeout(resolve, 0))
  })
}

describe('idle screen in the settings dialog', () => {
  it('sits in the Display section, with its controls', async () => {
    openConsole()
    act(() => {
      screen.getByRole('button', { name: 'Settings' }).click()
    })

    const dialog = screen.getByRole('dialog')
    const displayHeading = within(dialog).getByRole('heading', { name: 'Display' })
    const displaySection = displayHeading.closest('section') as HTMLElement
    expect(within(displaySection).getByText('Idle screen')).toBeInTheDocument()
    await settle()
    expect(within(displaySection).getByRole('button', { name: 'Upload picture' })).toBeEnabled()
    expect(within(displaySection).getByRole('button', { name: 'Remove picture' })).toBeDisabled()
  })

  it('reads the picture when the settings open, and not on connect', async () => {
    openConsole()
    await settle()
    expect(fetchMock.mock.calls.map(call => String(call[0]))).not.toContain('/api/idle-screen')
    fetchMock.mockClear()

    act(() => {
      screen.getByRole('button', { name: 'Settings' }).click()
    })

    await waitFor(() =>
      expect(fetchMock.mock.calls.map(call => String(call[0]))).toContain('/api/idle-screen')
    )
  })
})

describe('idle picture in the ON DISPLAY preview', () => {
  const withPicture: DisplaySettings = {
    verse_limit: 3,
    theme: 'dark',
    idle_image: { version: '0123456789abcdef', fit: 'logo' },
  }

  it('shows the picture the display shows while nothing is on screen', () => {
    openConsole(withPicture)

    const preview = screen.getByTestId('live-idle-picture')
    const image = preview.querySelector('img') as HTMLImageElement
    expect(image.getAttribute('src')).toBe('/api/idle-screen/image?v=0123456789abcdef')
    expect(image).toHaveClass('operator-idle-image--logo')
    expect(within(preview).getByText('Nothing on display')).toHaveClass('sr-only')
  })

  it('shows the empty message when no picture is set', () => {
    openConsole()

    expect(screen.queryByTestId('live-idle-picture')).not.toBeInTheDocument()
    expect(screen.getByText('Nothing on display')).not.toHaveClass('sr-only')
  })

  it('follows a settings echo that changes or removes the picture', () => {
    openConsole(withPicture)

    push({
      type: 'display_settings_confirmed',
      data: { ...withPicture, idle_image: { version: 'fedcba9876543210', fit: 'fill' } },
    })
    const image = screen.getByTestId('live-idle-picture').querySelector('img') as HTMLImageElement
    expect(image.getAttribute('src')).toBe('/api/idle-screen/image?v=fedcba9876543210')
    expect(image).toHaveClass('operator-idle-image--fill')

    push({ type: 'display_settings_confirmed', data: { ...withPicture, idle_image: null } })
    expect(screen.queryByTestId('live-idle-picture')).not.toBeInTheDocument()
  })

  it('falls back to the empty message when the picture cannot be loaded', () => {
    openConsole(withPicture)

    const image = screen.getByTestId('live-idle-picture').querySelector('img') as HTMLImageElement
    act(() => {
      image.dispatchEvent(new Event('error'))
    })

    expect(screen.queryByTestId('live-idle-picture')).not.toBeInTheDocument()
    expect(screen.getByText('Nothing on display')).toBeInTheDocument()
  })
})
