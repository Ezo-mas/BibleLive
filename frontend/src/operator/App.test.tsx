import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import App from './App'
import type { AutoMode, OperatorMessage } from '../shared/types'
import { createMockSocket, push } from '../test/operatorHarness'
import type { MockSocket } from '../test/operatorHarness'

/** Captured socket instance so tests can push server messages. */
let socket: MockSocket | null = null

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

/** The history tab, opened: its list is the only body in the tab panel. */
function historyPanel() {
  fireEvent.click(screen.getByRole('tab', { name: 'History' }))
  return within(screen.getByRole('tabpanel'))
}

/**
 * Open the settings dialog, where the display, theme, and auto-approve
 * preferences live. The dialog is where the server-owned values are rendered
 * now; nothing on the main screen controls them except the compact mode chip.
 */
function openSettings() {
  act(() => {
    screen.getByRole('button', { name: 'Settings' }).click()
  })
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
        socket = createMockSocket()
        return socket
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
  it('folds an approved reference into the history list', async () => {
    render(<App />)
    act(() => {
      socket?.onopen?.()
    })

    const panel = historyPanel()
    expect(panel.getByText('No history yet')).toBeInTheDocument()

    push(socket, { type: 'approval_confirmed', data: { reference: reference() } })

    await waitFor(() => expect(panel.getByText('John 1:1-3')).toBeInTheDocument())
    // The history tab carries no count, so the list itself is the only evidence.
    expect(screen.getByRole('tab', { name: 'History' })).not.toHaveTextContent(/\d/)
    expect(panel.queryByText('No history yet')).not.toBeInTheDocument()
  })

  it('prepends the newest approval ahead of older ones', async () => {
    render(<App />)
    act(() => {
      socket?.onopen?.()
    })

    push(socket, { type: 'approval_confirmed', data: { reference: reference() } })
    push(socket, {
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

    const panel = historyPanel()
    await waitFor(() => expect(panel.getByText('Romans 8:28')).toBeInTheDocument())

    const titles = screen
      .getAllByRole('heading', { level: 3 })
      .map(el => el.textContent)
      .filter(text => text === 'John 1:1-3' || text === 'Romans 8:28')
    expect(titles[0]).toBe('Romans 8:28')
    expect(titles).toHaveLength(2)
  })
})

describe('auto-approve mode', () => {
  it('mirrors the mode reported in the snapshot', async () => {
    render(<App />)
    act(() => {
      socket?.onopen?.()
    })

    openSettings()

    // Before any snapshot the panel assumes manual approval.
    expect(autoModeButton('Off')).toHaveAttribute('aria-pressed', 'true')

    push(socket, snapshot('medium_and_up'))

    await waitFor(() => expect(autoModeButton('Med+')).toHaveAttribute('aria-pressed', 'true'))
    expect(autoModeButton('Off')).toHaveAttribute('aria-pressed', 'false')
  })

  it('falls back to Off for a mode this build does not know', async () => {
    render(<App />)
    act(() => {
      socket?.onopen?.()
    })
    openSettings()

    push(socket, snapshot('whenever' as unknown as AutoMode))

    await waitFor(() => expect(autoModeButton('Off')).toHaveAttribute('aria-pressed', 'true'))
  })

  it('sends set_auto_mode when the operator picks a mode', async () => {
    render(<App />)
    act(() => {
      socket?.onopen?.()
    })
    openSettings()

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
    openSettings()

    push(socket, { type: 'auto_mode_confirmed', data: { auto_mode: 'high' } })

    await waitFor(() => expect(autoModeButton('High')).toHaveAttribute('aria-pressed', 'true'))
    expect(autoModeButton('Off')).toHaveAttribute('aria-pressed', 'false')
  })

  it('keeps the mode visible on the console with settings closed', async () => {
    render(<App />)
    act(() => {
      socket?.onopen?.()
    })

    // The main screen carries only the Off / On control; the detailed options live
    // in the dialog, which is not open here.
    const auto = within(screen.getByRole('group', { name: 'Auto-approve' }))
    expect(auto.getByRole('button', { name: 'Off' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.queryByRole('group', { name: 'Auto-approve mode' })).not.toBeInTheDocument()

    push(socket, snapshot('medium_and_up'))

    await waitFor(() =>
      expect(auto.getByRole('button', { name: 'On' })).toHaveAttribute('aria-pressed', 'true')
    )
    expect(auto.getByRole('button', { name: 'On' })).toHaveAttribute('title', 'Med+')
  })

  it('disables the selector while the socket is not open', () => {
    render(<App />)
    openSettings()

    expect(autoModeButton('High')).toBeDisabled()
  })
})
