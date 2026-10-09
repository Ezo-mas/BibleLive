import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import App from './App'
import type { BibleReference, OperatorMessage } from '../shared/types'

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

function reference(id = 'p-1', book = 'John', chapter = 1, verseStart = 1): BibleReference {
  return {
    id,
    book,
    chapter,
    verse_start: verseStart,
    verse_end: null,
    confidence: 'high',
    inferred: false,
    text: `${book} ${chapter} text`,
    verses: [{ verse: verseStart, text: `${book} ${chapter}:${verseStart}` }],
    created_at: '2026-01-01T10:00:00Z',
  }
}

/** Connect the socket and deliver a snapshot with one pending reference. */
function openWith() {
  render(<App />)
  act(() => {
    socket?.onopen?.()
  })
  push({
    type: 'state_snapshot',
    data: {
      current_display: null,
      pending: [reference()],
      history: [],
      pending_count: 1,
      history_count: 0,
      display_connections: 1,
      operator_connections: 1,
      display_settings: { verse_limit: 3, theme: 'dark' },
      auto_mode: 'off',
    },
  })
}

/** The verse-limit segmented control, scoped so 'All' cannot collide. */
function versesShown() {
  return within(screen.getByRole('group', { name: 'Verses shown on display' }))
}

beforeEach(() => {
  window.localStorage.clear()
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
  socket = null
})

describe('server-confirmed feedback', () => {
  it('claims nothing until the server confirms the approval', () => {
    openWith()

    act(() => {
      screen.getByRole('button', { name: /John 1:1/ }).click()
    })
    act(() => {
      screen.getByRole('button', { name: /approve and show/i }).click()
    })

    // The request is on the wire but nothing has come back: no success yet.
    expect(screen.queryByText(/^Shown:/)).not.toBeInTheDocument()

    push({ type: 'approval_confirmed', data: { reference: reference() } })

    expect(screen.getByRole('status')).toHaveTextContent('Shown: John 1:1')
  })

  it('replaces the previous notice instead of stacking a second one', () => {
    openWith()

    push({ type: 'approval_confirmed', data: { reference: reference() } })
    push({ type: 'clear_confirmed', data: {} })

    expect(screen.getAllByRole('status')).toHaveLength(1)
    expect(screen.getByRole('status')).toHaveTextContent('Display cleared')
  })

  it('reports clearing the queue', () => {
    openWith()

    push({ type: 'pending_cleared', data: {} })

    expect(screen.getByRole('status')).toHaveTextContent('Pending queue cleared')
  })

  it('lets the operator dismiss the notice by hand', () => {
    openWith()
    push({ type: 'clear_confirmed', data: {} })

    act(() => {
      screen.getByRole('button', { name: /dismiss message/i }).click()
    })

    expect(screen.queryByText('Display cleared')).not.toBeInTheDocument()
  })
})

describe('action failures', () => {
  it('surfaces a failed approval with the server reason', () => {
    openWith()

    push({
      type: 'error',
      data: { message: 'Reference not found or already processed', action: 'approve' },
    })

    const alert = screen.getByRole('alert')
    expect(alert).toHaveTextContent('Could not show that reference')
    expect(alert).toHaveTextContent('Reference not found or already processed')
  })

  it('reverts a rejected settings change and says so', async () => {
    openWith()

    act(() => {
      versesShown().getByRole('button', { name: '5' }).click()
    })
    expect(versesShown().getByRole('button', { name: '5' })).toHaveAttribute('aria-pressed', 'true')

    push({
      type: 'error',
      data: {
        message: "Invalid verse_limit; expected one of 3, 5, 10, 'all'",
        action: 'set_verse_limit',
      },
    })

    // The control must not keep claiming a value the display is not using.
    await waitFor(() =>
      expect(versesShown().getByRole('button', { name: '3' })).toHaveAttribute(
        'aria-pressed',
        'true'
      )
    )
    expect(versesShown().getByRole('button', { name: '5' })).toHaveAttribute(
      'aria-pressed',
      'false'
    )
    expect(screen.getByRole('alert')).toHaveTextContent('reverted')
  })

  it('falls back to the confirmed auto-approve mode when the change fails', async () => {
    openWith()

    act(() => {
      within(screen.getByRole('group', { name: 'Auto-approve mode' }))
        .getByRole('button', { name: 'All' })
        .click()
    })

    push({
      type: 'error',
      data: { message: "Invalid auto_mode; expected 'off'", action: 'set_auto_mode' },
    })

    await waitFor(() =>
      expect(
        within(screen.getByRole('group', { name: 'Auto-approve mode' })).getByRole('button', {
          name: 'Off',
        })
      ).toHaveAttribute('aria-pressed', 'true')
    )
    expect(screen.getByRole('alert')).toHaveTextContent('Could not change auto-approve')
  })
})

describe('settings confirmations', () => {
  it('announces what actually changed and stays quiet on an unchanged echo', () => {
    openWith()

    push({ type: 'display_settings_confirmed', data: { verse_limit: 3, theme: 'dark' } })
    expect(screen.queryByRole('status')).not.toBeInTheDocument()

    push({ type: 'display_settings_confirmed', data: { verse_limit: 5, theme: 'dark' } })

    expect(screen.getByRole('status')).toHaveTextContent('Saved — verses shown 5')
    expect(versesShown().getByRole('button', { name: '5' })).toHaveAttribute('aria-pressed', 'true')
  })

  it('explains which detections the active auto-approve mode accepts', () => {
    openWith()
    expect(screen.getByText('Every new detection waits for your approval.')).toBeInTheDocument()

    push({ type: 'auto_mode_confirmed', data: { auto_mode: 'medium_and_up' } })

    expect(
      screen.getByText('New high- and medium-confidence detections go straight to the display.')
    ).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('Auto-approve: Med+')
  })
})

describe('pending card clarity', () => {
  it('marks the staged card and shows when the detection arrived', () => {
    openWith()

    act(() => {
      screen.getByRole('button', { name: /John 1:1/ }).click()
    })

    const card = screen.getByRole('button', { name: /John 1:1/ })
    expect(within(card).getByText('Staged in Up Next')).toBeInTheDocument()
    expect(card).toHaveAttribute('data-confidence', 'high')

    const time = within(card).getByText(/\d{1,2}:\d{2}:\d{2}/)
    expect(time).toHaveAttribute('datetime', '2026-01-01T10:00:00Z')
  })
})
