import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
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

/**
 * Constructor stub for `WebSocket`. `OPEN` matters: the shared hook only sends
 * while `readyState === WebSocket.OPEN`, so a mock without that static swallows
 * every action silently.
 */
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

function reference(
  id: string,
  book: string,
  chapter: number,
  verseStart: number,
  verseEnd: number | null = null
): BibleReference {
  return {
    id,
    book,
    chapter,
    verse_start: verseStart,
    verse_end: verseEnd,
    confidence: 'high',
    inferred: false,
    text: `${book} ${chapter} text`,
    verses: [{ verse: verseStart, text: `${book} ${chapter}:${verseStart}` }],
    created_at: '2026-01-01T00:00:00Z',
  }
}

const pending = [
  reference('p-1', 'John', 1, 1),
  reference('p-2', 'Romans', 8, 28),
  reference('p-3', 'Luke', 1, 5),
]

/** Connect the socket and deliver a snapshot with the standard pending queue. */
function openWith(overrides: Partial<OperatorMessage & { type: 'state_snapshot' }> = {}) {
  render(<App />)
  act(() => {
    socket?.onopen?.()
  })
  push({
    type: 'state_snapshot',
    data: {
      current_display: null,
      pending,
      history: [],
      pending_count: pending.length,
      history_count: 0,
      display_connections: 1,
      operator_connections: 1,
      display_settings: { verse_limit: 3, theme: 'dark' },
      ...overrides,
    },
  })
}

function press(key: string) {
  act(() => {
    window.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true }))
  })
}

function approveButton() {
  return screen.getByRole('button', { name: 'Show on display' })
}

/**
 * Open the settings dialog. Display, theme, and auto-approve preferences live
 * there now, so a test of them is a test of the gear, the dialog, and the
 * controls inside it.
 */
function openSettings() {
  act(() => {
    screen.getByRole('button', { name: 'Settings' }).click()
  })
}

/** The last action sent to the server, parsed. */
function lastSent() {
  const calls = socket?.send.mock.calls ?? []
  return calls.length ? JSON.parse(calls[calls.length - 1][0]) : null
}

/** Operator actions sent after the authenticate first-message (empty: no token). */
function sentActions() {
  const calls = socket?.send.mock.calls ?? []
  return calls
    .map(([text]) => JSON.parse(text))
    .filter(message => message.action !== 'authenticate')
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

describe('pending selection and approval', () => {
  it('selects down/up the queue and approves the selected item by reference ID', () => {
    openWith()
    expect(approveButton()).toBeDisabled()

    press('ArrowDown')
    press('ArrowDown')
    expect(approveButton()).toBeEnabled()

    press('Enter')
    expect(lastSent()).toEqual({ action: 'approve', reference_id: 'p-2' })
  })

  it('selects the first item when arrowing up or down from no selection', () => {
    openWith()
    press('ArrowUp')
    press('Enter')
    expect(lastSent()).toEqual({ action: 'approve', reference_id: 'p-1' })
  })

  it('deselects on Escape and stops approving', () => {
    openWith()
    press('ArrowDown')
    expect(approveButton()).toBeEnabled()

    press('Escape')
    expect(approveButton()).toBeDisabled()

    press('Enter')
    expect(sentActions()).toEqual([])
  })

  it('selects a pending card by clicking it', () => {
    openWith()
    const card = screen.getByRole('button', { name: /Romans 8:28/ })
    act(() => {
      card.click()
    })
    expect(card).toHaveAttribute('aria-pressed', 'true')

    press('Enter')
    expect(lastSent()).toEqual({ action: 'approve', reference_id: 'p-2' })
  })

  it('keeps the staged selection when a queue update still holds it', () => {
    openWith()
    press('ArrowDown')
    expect(approveButton()).toBeEnabled()

    // A detection arriving mid-decision must not snatch the staged reference
    // away. Approval goes by ID, so keeping it cannot show the wrong passage.
    const grown = [...pending, reference('p-4', 'Acts', 2, 1)]
    push({ type: 'pending_update', data: { pending: grown, count: grown.length } })

    expect(approveButton()).toBeEnabled()
    expect(screen.getByTestId('next-preview')).toHaveTextContent('John 1:1')

    press('Enter')
    expect(lastSent()).toEqual({ action: 'approve', reference_id: 'p-1' })
  })

  it('drops the selection when the queue update no longer holds it', () => {
    openWith()
    press('ArrowDown')
    expect(approveButton()).toBeEnabled()

    push({ type: 'pending_update', data: { pending: [reference('p-9', 'Acts', 2, 1)], count: 1 } })
    expect(approveButton()).toBeDisabled()
  })

  it('approves nothing when a resumed queue no longer holds the selected ID', () => {
    openWith()
    press('ArrowDown')
    press('ArrowDown')
    push({
      type: 'pending_update',
      data: { pending: [reference('p-9', 'Acts', 2, 1)], count: 1 },
    })
    press('Enter')
    expect(sentActions()).toEqual([])
  })
})

describe('keyboard shortcuts while Settings is open', () => {
  it('does not approve the staged reference on Enter until Settings is closed', () => {
    openWith()
    press('ArrowDown')
    expect(approveButton()).toBeEnabled()

    openSettings()

    // The dialog owns the keyboard while it is open: a keypress sent from
    // behind it must neither reach the server nor move the staged selection.
    press('Enter')
    expect(sentActions()).toEqual([])
    expect(screen.getByRole('button', { name: /John 1:1/ })).toHaveAttribute('aria-pressed', 'true')

    // Closed, the same keypress approves the staged reference again by ID — the
    // dialog stood the shortcut down, it did not break it.
    act(() => {
      within(screen.getByRole('dialog')).getByRole('button', { name: 'Close settings' }).click()
    })
    press('Enter')
    expect(lastSent()).toEqual({ action: 'approve', reference_id: 'p-1' })
  })
})

describe('clear actions', () => {
  it('enables Clear Display only while the display has content', () => {
    openWith()
    const clearDisplay = screen.getByRole('button', { name: /clear display/i })
    expect(clearDisplay).toBeDisabled()

    push({ type: 'approval_confirmed', data: { reference: reference('p-1', 'John', 1, 1) } })
    expect(clearDisplay).toBeEnabled()

    act(() => {
      clearDisplay.click()
    })
    expect(lastSent()).toEqual({ action: 'clear' })

    push({ type: 'clear_confirmed', data: {} })
    expect(clearDisplay).toBeDisabled()
  })

  it('clears the queue only after the operator confirms', () => {
    openWith()
    const clearQueue = screen.getByRole('button', { name: /clear queue/i })
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)

    act(() => {
      clearQueue.click()
    })
    expect(confirm).toHaveBeenCalled()
    expect(sentActions()).toEqual([])

    confirm.mockReturnValue(true)
    act(() => {
      clearQueue.click()
    })
    expect(lastSent()).toEqual({ action: 'clear_pending' })
  })
})

describe('display settings', () => {
  it('switches the verse limit and background optimistically', async () => {
    openWith()
    openSettings()
    const five = screen.getByRole('button', { name: '5' })
    act(() => {
      five.click()
    })
    expect(lastSent()).toEqual({ action: 'set_verse_limit', verse_limit: 5 })
    await waitFor(() => expect(five).toHaveAttribute('aria-pressed', 'true'))

    const light = screen.getByRole('button', { name: 'Light' })
    act(() => {
      light.click()
    })
    expect(lastSent()).toEqual({ action: 'set_display_theme', theme: 'light' })
    await waitFor(() => expect(light).toHaveAttribute('aria-pressed', 'true'))
  })

  it('disables settings controls while disconnected', () => {
    render(<App />)
    openSettings()
    // 'All' also labels an auto-approve option, so scope to the verse-limit group.
    const verseLimits = within(screen.getByRole('group', { name: 'Verses shown on display' }))
    expect(verseLimits.getByRole('button', { name: '5' })).toBeDisabled()
    expect(verseLimits.getByRole('button', { name: 'All' })).toBeDisabled()

    const autoModes = within(screen.getByRole('group', { name: 'Auto-approve mode' }))
    expect(autoModes.getByRole('button', { name: 'High' })).toBeDisabled()
  })
})

describe('authentication', () => {
  it('prompts for a token, without the failure text, when none was tried', () => {
    render(<App />)
    act(() => {
      socket?.onclose?.({ code: 1008 })
    })
    // A 1008 with no token tried means the server wants one, not that one
    // failed: the prompt opens but there is nothing to call failed.
    expect(screen.queryByText(/authentication failed/i)).not.toBeInTheDocument()
    expect(screen.getByLabelText(/operator token/i)).toBeInTheDocument()
  })

  it('connects with the submitted token and clears the prompt', () => {
    render(<App />)
    act(() => {
      socket?.onclose?.({ code: 1008 })
    })
    const input = screen.getByLabelText(/operator token/i)
    fireEvent.change(input, { target: { value: 'secret' } })
    act(() => {
      screen.getByRole('button', { name: /connect/i }).click()
    })
    expect(window.localStorage.getItem('biblelive_operator_token')).toBe('secret')
    expect(screen.queryByLabelText(/operator token/i)).not.toBeInTheDocument()
  })
})
