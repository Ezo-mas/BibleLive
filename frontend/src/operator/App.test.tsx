import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, render, screen, waitFor } from '@testing-library/react'
import App from './App'
import type { OperatorMessage } from '../shared/types'

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

/** Read the numeric value shown next to a statistics label. */
function statValue(label: string) {
  return screen.getByText(label).nextElementSibling?.textContent
}

beforeEach(() => {
  window.localStorage.clear()
  vi.stubGlobal('WebSocket', function MockWebSocket() {
    return createMockSocket()
  })
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

    await waitFor(() => expect(screen.getByText('John 1:1-3')).toBeInTheDocument())
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

    await waitFor(() => expect(screen.getByText('Romans 8:28')).toBeInTheDocument())

    const titles = screen
      .getAllByRole('heading', { level: 3 })
      .map(el => el.textContent)
      .filter(text => text === 'John 1:1-3' || text === 'Romans 8:28')
    expect(titles[0]).toBe('Romans 8:28')
    expect(statValue('History')).toBe('2')
  })
})
