import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, render, screen } from '@testing-library/react'
import App from './App'
import type { BibleReference, FollowAlongHint, OperatorMessage } from '../shared/types'

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

function reference(id: string, verses: number[]): BibleReference {
  return {
    id,
    book: 'Luke',
    chapter: 16,
    verse_start: verses[0],
    verse_end: verses[verses.length - 1],
    confidence: 'high',
    inferred: false,
    text: 'Luke 16 passage',
    verses: verses.map(verse => ({ verse, text: `Luke 16:${verse} text` })),
    source: 'manual',
    created_at: '2026-01-01T00:00:00Z',
  }
}

const onScreen = reference('d-1', [10, 11, 12])

function hintFor(displayId: string, overrides: Partial<FollowAlongHint> = {}): OperatorMessage {
  return {
    type: 'follow_along',
    data: {
      book: 'Luke',
      chapter: 16,
      verse_start: 13,
      verse_end: 15,
      display_id: displayId,
      ...overrides,
    },
  }
}

function openWith(currentDisplay: BibleReference | null) {
  render(<App />)
  act(() => {
    socket?.onopen?.()
  })
  push({
    type: 'state_snapshot',
    data: {
      current_display: currentDisplay,
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

function sentActions() {
  const calls = socket?.send.mock.calls ?? []
  return calls
    .map(([text]) => JSON.parse(text))
    .filter(message => message.action !== 'authenticate')
}

const hintRegion = () => screen.queryByRole('region', { name: 'Follow-along suggestion' })

beforeEach(() => {
  window.localStorage.clear()
  vi.stubGlobal('WebSocket', MockWebSocket)
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

describe('follow-along hint', () => {
  it('shows nothing until a hint arrives', () => {
    openWith(onScreen)

    expect(hintRegion()).not.toBeInTheDocument()
  })

  it('renders the hint for the passage on screen', () => {
    openWith(onScreen)
    push(hintFor('d-1'))

    expect(hintRegion()).toBeInTheDocument()
    expect(screen.getByText('Reading now')).toBeInTheDocument()
    expect(screen.getByText('Luke 16:13-15')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Show Luke 16:13-15 on display' })).toBeEnabled()
  })

  it('writes a one-verse hint without a range', () => {
    openWith(onScreen)
    push(hintFor('d-1', { verse_start: 13, verse_end: 13 }))

    expect(screen.getByText('Luke 16:13')).toBeInTheDocument()
  })

  it('ignores a hint for a display that is no longer the current one', () => {
    openWith(onScreen)
    push(hintFor('some-older-display'))

    expect(hintRegion()).not.toBeInTheDocument()
  })

  it('ignores a hint when nothing is on the display', () => {
    openWith(null)
    push(hintFor('d-1'))

    expect(hintRegion()).not.toBeInTheDocument()
  })

  it('drops the hint when the display changes to another passage', () => {
    openWith(onScreen)
    push(hintFor('d-1'))
    expect(hintRegion()).toBeInTheDocument()

    push({ type: 'display_confirmed', data: { reference: reference('d-2', [20]) } })

    expect(hintRegion()).not.toBeInTheDocument()
  })

  it('drops the hint when the display is cleared', () => {
    openWith(onScreen)
    push(hintFor('d-1'))

    push({ type: 'clear_confirmed', data: {} })

    expect(hintRegion()).not.toBeInTheDocument()
  })

  it('drops the hint on a fresh snapshot, which never carries one', () => {
    openWith(onScreen)
    push(hintFor('d-1'))

    push({
      type: 'state_snapshot',
      data: {
        current_display: onScreen,
        pending: [],
        history: [],
        pending_count: 0,
        history_count: 0,
        display_connections: 1,
        operator_connections: 1,
      },
    })

    expect(hintRegion()).not.toBeInTheDocument()
  })

  it('replaces an earlier hint with the newest one', () => {
    openWith(onScreen)
    push(hintFor('d-1'))
    push(hintFor('d-1', { verse_start: 14, verse_end: 16 }))

    expect(screen.queryByText('Luke 16:13-15')).not.toBeInTheDocument()
    expect(screen.getByText('Luke 16:14-16')).toBeInTheDocument()
  })

  it('Show sends display_reference with exactly the hint coordinates', () => {
    openWith(onScreen)
    push(hintFor('d-1'))

    act(() => {
      screen.getByRole('button', { name: 'Show Luke 16:13-15 on display' }).click()
    })

    expect(sentActions()).toEqual([
      {
        action: 'display_reference',
        book: 'Luke',
        chapter: 16,
        verse_start: 13,
        verse_end: 15,
      },
    ])
  })

  it('does not send a second request while the first is waiting on the server', () => {
    openWith(onScreen)
    push(hintFor('d-1'))
    const show = screen.getByRole('button', { name: 'Show Luke 16:13-15 on display' })

    act(() => show.click())

    expect(show).toBeDisabled()
    act(() => show.click())
    expect(sentActions()).toHaveLength(1)
  })

  it('goes away once the server confirms the passage is on screen', () => {
    openWith(onScreen)
    push(hintFor('d-1'))
    act(() => {
      screen.getByRole('button', { name: 'Show Luke 16:13-15 on display' }).click()
    })

    push({
      type: 'display_confirmed',
      data: { reference: { ...reference('d-2', [13, 14, 15]) } },
    })

    expect(hintRegion()).not.toBeInTheDocument()
  })

  it('stays, and can be retried, when the server refuses the request', () => {
    openWith(onScreen)
    push(hintFor('d-1'))
    act(() => {
      screen.getByRole('button', { name: 'Show Luke 16:13-15 on display' }).click()
    })
    vi.spyOn(console, 'error').mockImplementation(() => {})

    push({
      type: 'error',
      data: { message: 'Passage not found', action: 'display_reference' },
    })

    expect(screen.getByRole('button', { name: 'Show Luke 16:13-15 on display' })).toBeEnabled()
  })
})
