import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import App from './App'
import type { AutoMode, OperatorMessage } from '../shared/types'

/** The subset of WebSocket the operator app uses. */
interface MockSocket {
  onopen: (() => void) | null
  onmessage: ((event: { data: string }) => void) | null
  onclose: ((event: { code: number }) => void) | null
  onerror: ((event: unknown) => void) | null
  readyState: number
  send: (data: string) => void
  close: () => void
}

/** Captured socket instance so tests can push server messages. */
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

/** Deliver a server message to the operator app. */
function push(message: OperatorMessage) {
  act(() => {
    socket?.onmessage?.({ data: JSON.stringify(message) })
  })
}

function reference() {
  return {
    id: 'ref-1',
    book: 'John',
    chapter: 1,
    verse_start: 1,
    verse_end: 3,
    confidence: 'high' as const,
    inferred: false,
    text: '',
    verses: [
      { verse: 1, text: 'In the beginning was the Word.' },
      { verse: 2, text: 'The same was in the beginning with God.' },
      { verse: 3, text: 'All things were made by him.' },
    ],
    created_at: '2026-01-01T00:00:00Z',
  }
}

/** The history panel, scoped so LIVE showing the same reference cannot collide. */
function historyPanel() {
  const section = screen.getByRole('heading', { name: 'Recent History' }).closest('section')
  if (!section) throw new Error('history section not found')
  return within(section)
}

/** Read the numeric value shown next to a statistics label. */
function statValue(label: string) {
  return screen.getByText(label).nextElementSibling?.textContent
}

/** A state snapshot carrying the given auto-approve mode. */
function snapshot(autoMode: AutoMode): OperatorMessage {
  return {
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
      auto_mode: autoMode,
    },
  }
}

/** The auto-approve option button with the given label. */
function autoModeButton(label: string) {
  const group = screen.getByRole('group', { name: 'Auto-approve mode' })
  return within(group).getByRole('button', { name: label })
}

/** The socket's send spy, so tests can assert what the panel sent. */
function sentMessages() {
  const send = socket?.send as unknown as ReturnType<typeof vi.fn>
  return send.mock.calls.map(call => JSON.parse(call[0] as string))
}

beforeEach(() => {
  window.localStorage.clear()
  // OPEN is what useWebSocket compares readyState against before sending.
  vi.stubGlobal(
    'WebSocket',
    Object.assign(
      function MockWebSocket() {
        return createMockSocket()
      },
      { OPEN: 1 }
    )
  )
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
  socket = null
})

describe('operator approval history', () => {
  it('folds an approved reference into history and increments shown/history', async () => {
    render(<App />)
    act(() => {
      socket?.onopen?.()
    })

    expect(statValue('Shown')).toBe('0')
    expect(statValue('History')).toBe('0')
    expect(screen.getByText('No history yet')).toBeInTheDocument()

    push({ type: 'approval_confirmed', data: { reference: reference() } })

    await waitFor(() => expect(historyPanel().getByText('John 1:1-3')).toBeInTheDocument())
    expect(statValue('Shown')).toBe('1')
    expect(statValue('History')).toBe('1')
    expect(screen.queryByText('No history yet')).not.toBeInTheDocument()
  })

  it('prepends the newest approval ahead of older ones', async () => {
    render(<App />)
    act(() => {
      socket?.onopen?.()
    })

    push({ type: 'approval_confirmed', data: { reference: reference() } })
    push({
      type: 'approval_confirmed',
      data: {
        reference: {
          ...reference(),
          id: 'ref-2',
          book: 'Romans',
          chapter: 8,
          verse_start: 28,
          verse_end: null,
        },
      },
    })

    await waitFor(() => expect(historyPanel().getByText('Romans 8:28')).toBeInTheDocument())

    const titles = screen
      .getAllByRole('heading', { level: 3 })
      .map(el => el.textContent)
      .filter(text => text === 'John 1:1-3' || text === 'Romans 8:28')
    expect(titles[0]).toBe('Romans 8:28')
    expect(statValue('History')).toBe('2')
  })
})

describe('auto-approve mode', () => {
  it('mirrors the mode reported in the snapshot', async () => {
    render(<App />)
    act(() => {
      socket?.onopen?.()
    })

    // Before any snapshot the panel assumes manual approval.
    expect(autoModeButton('Off')).toHaveAttribute('aria-pressed', 'true')

    push(snapshot('medium_and_up'))

    await waitFor(() => expect(autoModeButton('Med+')).toHaveAttribute('aria-pressed', 'true'))
    expect(autoModeButton('Off')).toHaveAttribute('aria-pressed', 'false')
  })

  it('falls back to Off for a mode this build does not know', async () => {
    render(<App />)
    act(() => {
      socket?.onopen?.()
    })

    push(snapshot('whenever' as unknown as AutoMode))

    await waitFor(() => expect(autoModeButton('Off')).toHaveAttribute('aria-pressed', 'true'))
  })

  it('sends set_auto_mode when the operator picks a mode', async () => {
    render(<App />)
    act(() => {
      socket?.onopen?.()
    })

    fireEvent.click(autoModeButton('All'))

    expect(sentMessages()).toContainEqual({ action: 'set_auto_mode', auto_mode: 'all' })
    // Optimistic, so the control responds before the server confirms.
    expect(autoModeButton('All')).toHaveAttribute('aria-pressed', 'true')
  })

  it('applies the mode the server confirms', async () => {
    render(<App />)
    act(() => {
      socket?.onopen?.()
    })

    push({ type: 'auto_mode_confirmed', data: { auto_mode: 'high' } })

    await waitFor(() => expect(autoModeButton('High')).toHaveAttribute('aria-pressed', 'true'))
    expect(autoModeButton('Off')).toHaveAttribute('aria-pressed', 'false')
  })

  it('disables the selector while the socket is not open', () => {
    render(<App />)

    expect(autoModeButton('High')).toBeDisabled()
  })
})
