import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { act, render, screen, within } from '@testing-library/react'
import App from './App'
import type { BibleReference, DisplaySettings } from '../shared/types'
import { createMockSocket, push } from '../test/operatorHarness'
import type { MockSocket } from '../test/operatorHarness'

let socket: MockSocket | null = null

const MockWebSocket = Object.assign(
  function MockWebSocketStub() {
    socket = createMockSocket()
    return socket
  },
  { OPEN: 1, CLOSING: 2, CLOSED: 3 }
)

const passage: BibleReference = {
  id: 'ref-1',
  book: 'John',
  chapter: 3,
  verse_start: 16,
  verse_end: 16,
  confidence: null,
  source: 'manual',
  inferred: false,
  text: 'For God so loved the world.',
  verses: [{ verse: 16, text: 'For God so loved the world.' }],
  created_at: '2026-01-01T00:00:00Z',
}

function sentMessages() {
  const send = socket?.send as unknown as ReturnType<typeof vi.fn>
  return send.mock.calls.map(call => JSON.parse(call[0] as string))
}

function settings(live?: boolean): DisplaySettings {
  return { verse_limit: 3, theme: 'dark', ...(live === undefined ? {} : { live }) }
}

function openWith(display_settings: DisplaySettings) {
  render(<App />)
  act(() => {
    socket?.onopen?.()
  })
  push(socket, {
    type: 'state_snapshot',
    data: {
      current_display: passage,
      pending: [],
      history: [],
      pending_count: 0,
      history_count: 0,
      display_connections: 1,
      operator_connections: 1,
      display_settings,
      auto_mode: 'off',
    },
  })
}

function liveSwitch() {
  const group = within(screen.getByRole('group', { name: 'Audience display on air' }))
  return {
    live: group.getByRole('button', { name: 'Live' }),
    off: group.getByRole('button', { name: 'Off air' }),
  }
}

beforeEach(() => {
  window.localStorage.clear()
  vi.stubGlobal('WebSocket', MockWebSocket)
  // jsdom has no matchMedia; the theme hook reads it.
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
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe = vi.fn()
      disconnect = vi.fn()
    }
  )
})

afterEach(() => {
  vi.unstubAllGlobals()
  socket = null
})

it('starts live and moves only when the server confirms', () => {
  openWith(settings(true))
  expect(liveSwitch().live).toHaveAttribute('aria-pressed', 'true')
  expect(screen.queryByTestId('off-air-badge')).not.toBeInTheDocument()

  act(() => {
    liveSwitch().off.click()
  })
  expect(sentMessages().at(-1)).toEqual({ action: 'set_live', live: false })
  // Not optimistic: until the server agrees, the console still says live.
  expect(liveSwitch().live).toHaveAttribute('aria-pressed', 'true')

  push(socket, { type: 'display_settings_confirmed', data: settings(false) })
  expect(liveSwitch().off).toHaveAttribute('aria-pressed', 'true')
  expect(screen.getByTestId('off-air-badge')).toHaveAttribute(
    'title',
    'The audience sees a black screen'
  )
  expect(screen.getByRole('status')).toHaveTextContent('Display off air')
  // The preview keeps the passage: off air is where the operator checks it.
  expect(screen.getByText(passage.text)).toBeInTheDocument()

  act(() => {
    liveSwitch().live.click()
  })
  expect(sentMessages().at(-1)).toEqual({ action: 'set_live', live: true })
  push(socket, { type: 'display_settings_confirmed', data: settings(true) })
  expect(liveSwitch().live).toHaveAttribute('aria-pressed', 'true')
  expect(screen.queryByTestId('off-air-badge')).not.toBeInTheDocument()
})

it('a rejected switch stays where the server left it and says why', () => {
  openWith(settings(true))
  act(() => {
    liveSwitch().off.click()
  })
  push(socket, {
    type: 'error',
    data: { message: 'Invalid live; expected true or false', action: 'set_live' },
  })
  expect(liveSwitch().live).toHaveAttribute('aria-pressed', 'true')
  expect(screen.getByRole('alert')).toHaveTextContent('Could not switch the display on or off air')
})

it('is read-only while disconnected', () => {
  openWith(settings(true))
  act(() => {
    socket?.onclose?.({ code: 1006 } as CloseEvent)
  })
  expect(liveSwitch().live).toBeDisabled()
  expect(liveSwitch().off).toBeDisabled()
})

it('shows off air from the snapshot, as a console that connects mid-blackout must', () => {
  openWith(settings(false))
  expect(liveSwitch().off).toHaveAttribute('aria-pressed', 'true')
  expect(screen.getByTestId('off-air-badge')).toBeInTheDocument()
})

it('treats a snapshot without the field, from an older server, as live', () => {
  openWith(settings())
  expect(liveSwitch().live).toHaveAttribute('aria-pressed', 'true')
})

it('pressing the side already chosen sends nothing', () => {
  openWith(settings(true))
  const before = sentMessages().length
  act(() => {
    liveSwitch().live.click()
  })
  expect(sentMessages()).toHaveLength(before)
})
