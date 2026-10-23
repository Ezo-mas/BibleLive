import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, render, screen, within } from '@testing-library/react'
import App from './App'
import type { BibleReference, OperatorMessage, VerseLimit } from '../shared/types'

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

/** A reference with one verse per number given. */
function reference(id: string, book: string, chapter: number, verses: number[]): BibleReference {
  return {
    id,
    book,
    chapter,
    verse_start: verses[0],
    verse_end: verses[verses.length - 1],
    confidence: 'high',
    inferred: false,
    text: `${book} ${chapter} passage`,
    verses: verses.map(verse => ({ verse, text: `${book} ${chapter}:${verse} text` })),
    created_at: '2026-01-01T00:00:00Z',
  }
}

/** One verse longer than the default display limit. */
const psalms = reference('d-1', 'Psalms', 23, [1, 2, 3, 4, 5])
const john = reference('p-1', 'John', 1, [1])
const romans = reference('p-2', 'Romans', 8, [28])

const queue = [john, romans]

/** Snapshot with the given display state, delivered after the socket opens. */
function openWith(
  options: {
    currentDisplay?: BibleReference | null
    verseLimit?: VerseLimit
    pending?: BibleReference[]
  } = {}
) {
  const { currentDisplay = null, verseLimit = 3, pending = queue } = options

  render(<App />)
  act(() => {
    socket?.onopen?.()
  })
  push({
    type: 'state_snapshot',
    data: {
      current_display: currentDisplay,
      pending,
      history: [],
      pending_count: pending.length,
      history_count: 0,
      display_connections: 1,
      operator_connections: 1,
      display_settings: { verse_limit: verseLimit, theme: 'dark' },
    },
  })
}

/** Select the pending card whose title matches, staging it as NEXT. */
function selectCard(name: RegExp) {
  act(() => {
    screen.getByRole('button', { name }).click()
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

describe('LIVE panel', () => {
  it('renders the reference the server reports is on the display', () => {
    openWith({ currentDisplay: psalms })

    const verses = within(screen.getByTestId('live-verses'))
    expect(verses.getByText('Psalms 23:1 text')).toBeInTheDocument()
    // The citation is on the monitor, under the verses, as on the projector; the
    // card's header carries only its label.
    expect(screen.getByText('Psalms 23:1-5')).toBeInTheDocument()
    // The ON DISPLAY label is the only status; no chip repeats it.
    expect(screen.queryByText('Live')).not.toBeInTheDocument()
  })

  it('labels the preview as a group that still exposes the verse text', () => {
    openWith({ currentDisplay: psalms })

    const preview = screen.getByRole('group', { name: 'Audience display preview' })
    expect(within(preview).getByText('Psalms 23:1 text')).toBeInTheDocument()
  })

  it('shows an empty state when nothing is on the display', () => {
    openWith()

    expect(screen.getByText('Nothing on display')).toBeInTheDocument()
    expect(screen.queryByText('Idle')).not.toBeInTheDocument()
    expect(screen.queryByTestId('live-verses')).not.toBeInTheDocument()
  })

  it('caps the verses at the display limit and reports the hidden ones', () => {
    openWith({ currentDisplay: psalms, verseLimit: 3 })

    const verses = within(screen.getByTestId('live-verses'))
    expect(verses.getAllByText(/^Psalms 23:/)).toHaveLength(3)
    expect(verses.queryByText('Psalms 23:4 text')).not.toBeInTheDocument()
    expect(screen.getByText('+2 more verses')).toBeInTheDocument()
  })

  it('moves an approved reference onto the display', () => {
    openWith()

    push({ type: 'approval_confirmed', data: { reference: psalms } })

    expect(
      within(screen.getByTestId('live-verses')).getByText('Psalms 23:1 text')
    ).toBeInTheDocument()
    expect(screen.queryByText('Nothing on display')).not.toBeInTheDocument()
  })

  it('returns to the empty state when the display is cleared', () => {
    openWith({ currentDisplay: psalms })

    push({ type: 'clear_confirmed', data: {} })

    expect(screen.getByText('Nothing on display')).toBeInTheDocument()
  })

  it('previews the display theme the projectors are using', () => {
    openWith({ currentDisplay: psalms })
    expect(screen.getByTestId('live-stage')).toHaveAttribute('data-theme', 'dark')

    push({ type: 'display_settings_confirmed', data: { verse_limit: 3, theme: 'light' } })

    expect(screen.getByTestId('live-stage')).toHaveAttribute('data-theme', 'light')
  })
})

describe('NEXT panel', () => {
  it('shows nothing staged until a reference is selected', () => {
    openWith()

    expect(screen.getByText('Nothing staged')).toBeInTheDocument()
    expect(screen.queryByTestId('next-preview')).not.toBeInTheDocument()
  })

  it('stages the selected queue item without touching the display', () => {
    openWith({ currentDisplay: psalms })

    selectCard(/Romans 8:28/)

    const staged = within(screen.getByTestId('next-preview'))
    expect(staged.getByText('Romans 8:28')).toBeInTheDocument()
    expect(
      within(screen.getByTestId('next-verses')).getByText('Romans 8:28 text')
    ).toBeInTheDocument()

    // LIVE still shows what the projector showed before; nothing was sent.
    expect(
      within(screen.getByTestId('live-verses')).getByText('Psalms 23:1 text')
    ).toBeInTheDocument()
    expect(screen.queryByText('Nothing staged')).not.toBeInTheDocument()
    expect(sentActions()).toEqual([])
  })

  it('reports verses hidden by the current limit', () => {
    openWith({ pending: [psalms] })
    selectCard(/Psalms 23/)

    expect(screen.getByText('+2 more verses hidden by the current limit')).toBeInTheDocument()
  })

  it('shows the staged reference on approval and then stages nothing', () => {
    openWith()

    selectCard(/Romans 8:28/)
    act(() => {
      screen.getByRole('button', { name: 'Show on display' }).click()
    })
    expect(lastSent()).toEqual({ action: 'approve', reference_id: 'p-2' })

    // The server confirms the approval, then sends the queue without it.
    push({ type: 'approval_confirmed', data: { reference: romans } })
    push({ type: 'pending_update', data: { pending: [john], count: 1 } })

    expect(
      within(screen.getByTestId('live-verses')).getByText('Romans 8:28 text')
    ).toBeInTheDocument()
    expect(screen.getByText('Nothing staged')).toBeInTheDocument()
  })

  it('discards the staged reference without touching the display', () => {
    openWith({ currentDisplay: psalms })
    selectCard(/Romans 8:28/)

    act(() => {
      screen.getByRole('button', { name: 'Unstage' }).click()
    })

    expect(screen.getByText('Nothing staged')).toBeInTheDocument()
    expect(
      within(screen.getByTestId('live-verses')).getByText('Psalms 23:1 text')
    ).toBeInTheDocument()
    expect(sentActions()).toEqual([])
  })

  it('disables Show while the socket is not open', () => {
    render(<App />)
    push({
      type: 'state_snapshot',
      data: {
        current_display: null,
        pending: queue,
        history: [],
        pending_count: queue.length,
        history_count: 0,
        display_connections: 0,
        operator_connections: 1,
        display_settings: { verse_limit: 3, theme: 'dark' },
      },
    })
    selectCard(/Romans 8:28/)

    expect(screen.getByRole('button', { name: 'Show on display' })).toBeDisabled()
  })
})
