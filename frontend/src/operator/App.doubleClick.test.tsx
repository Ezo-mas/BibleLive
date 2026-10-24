import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen } from '@testing-library/react'
import App from './App'
import type { BibleReference } from '../shared/types'
import { createMockSocket, lastSent, push } from '../test/operatorHarness'
import type { MockSocket } from '../test/operatorHarness'

let socket: MockSocket | null = null

/** Constructor stub; `OPEN` is what the shared hook compares readyState to. */
const MockWebSocket = Object.assign(
  function MockWebSocketStub() {
    socket = createMockSocket()
    return socket
  },
  { OPEN: 1, CLOSING: 2, CLOSED: 3 }
)

/** Operator actions sent after the authenticate first-message (empty: no token). */
function sentActions() {
  const calls = socket?.send.mock.calls ?? []
  return calls
    .map(([text]) => JSON.parse(text))
    .filter(message => message.action !== 'authenticate')
}

/** A reference as the server reports one, whether queued, live, or in history. */
function entry(
  id: string,
  book: string,
  chapter: number,
  verseStart: number,
  source: 'detected' | 'manual' = 'detected'
): BibleReference {
  return {
    id,
    book,
    chapter,
    verse_start: verseStart,
    verse_end: verseStart,
    confidence: source === 'manual' ? null : 'high',
    inferred: false,
    source,
    text: `${book} ${chapter}:${verseStart} text`,
    verses: [{ verse: verseStart, text: `${book} ${chapter}:${verseStart} text` }],
    created_at: '2026-01-01T10:00:00Z',
  }
}

const psalms = entry('h-1', 'Psalms', 23, 1)
const john = entry('h-2', 'John', 3, 16)
const romans = entry('h-3', 'Romans', 8, 28, 'manual')
const history = [romans, john, psalms]
const live = entry('d-9', 'Luke', 2, 11)
const queued = entry('p-1', 'John', 1, 1)
const queuedAgain = entry('p-2', 'Acts', 2, 38)

function openWith({
  currentDisplay = null,
  pending = [],
  entries = [],
}: {
  currentDisplay?: BibleReference | null
  pending?: BibleReference[]
  entries?: BibleReference[]
} = {}) {
  render(<App />)
  act(() => {
    socket?.onopen?.()
  })
  push(socket, {
    type: 'state_snapshot',
    data: {
      current_display: currentDisplay,
      pending,
      history: entries,
      pending_count: pending.length,
      history_count: entries.length,
      display_connections: 1,
      operator_connections: 1,
      display_settings: { verse_limit: 3, theme: 'dark' },
    },
  })
}

/** The history tab's card whose reference matches. */
function historyCard(name: RegExp) {
  fireEvent.click(screen.getByRole('tab', { name: 'History' }))
  return screen.getByRole('button', { name })
}

/** The pending queue's card whose reference matches (the Pending tab is the default). */
function queuedCard(name: RegExp) {
  return screen.getByRole('button', { name })
}

function show() {
  act(() => {
    screen.getByRole('button', { name: 'Show on display' }).click()
  })
}

beforeEach(() => {
  window.localStorage.clear()
  vi.stubGlobal('WebSocket', MockWebSocket)
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
  vi.restoreAllMocks()
  socket = null
})

describe('double-click on a history entry', () => {
  it('a click, a click and a double-click send one display_reference with coordinates', () => {
    openWith({ currentDisplay: live, entries: history })
    const card = historyCard(/John 3:16/)

    fireEvent.click(card)
    fireEvent.click(card)
    fireEvent.doubleClick(card)

    expect(sentActions()).toEqual([
      { action: 'display_reference', book: 'John', chapter: 3, verse_start: 16, verse_end: 16 },
    ])
  })

  it('a repeated double-click while the request is in flight sends nothing more', () => {
    openWith({ currentDisplay: live, entries: history })
    const card = historyCard(/John 3:16/)

    fireEvent.doubleClick(card)
    fireEvent.doubleClick(card)

    expect(sentActions()).toHaveLength(1)
  })

  it('a double-click alone shows the entry at once, staging nothing', () => {
    openWith({ currentDisplay: live, entries: history })
    const card = historyCard(/John 3:16/)

    fireEvent.doubleClick(card)

    expect(lastSent(socket)).toEqual({
      action: 'display_reference',
      book: 'John',
      chapter: 3,
      verse_start: 16,
      verse_end: 16,
    })
    expect(screen.getByText('Nothing staged')).toBeInTheDocument()
    // Nothing is on the display until the server confirms it.
    expect(screen.getByTestId('live-verses')).toHaveTextContent('Luke 2:11 text')

    push(socket, {
      type: 'display_confirmed',
      data: { reference: entry('m-1', 'John', 3, 16, 'manual') },
    })
    expect(screen.getByText('Shown: John 3:16 (manual)')).toBeInTheDocument()
  })

  it('a rejected double-click reports the refusal and can be tried again', () => {
    openWith({ currentDisplay: live, entries: history })
    const card = historyCard(/John 3:16/)

    fireEvent.doubleClick(card)
    push(socket, {
      type: 'error',
      data: { action: 'display_reference', message: 'John 3:16 is not in the Bible text' },
    })
    expect(screen.getByRole('alert')).toHaveTextContent(
      'Could not show that passage: John 3:16 is not in the Bible text'
    )

    fireEvent.doubleClick(card)
    expect(sentActions()).toHaveLength(2)
  })
})

describe('double-click on a queued detection', () => {
  it('a click, a click and a double-click approve the detection once, by its ID', () => {
    openWith({ currentDisplay: live, pending: [queued] })
    const card = queuedCard(/John 1:1/)

    fireEvent.click(card)
    fireEvent.click(card)
    fireEvent.doubleClick(card)
    fireEvent.doubleClick(card)

    expect(sentActions()).toEqual([{ action: 'approve', reference_id: 'p-1' }])
  })

  it('a double-click alone approves at once, staging nothing', () => {
    openWith({ currentDisplay: live, pending: [queued] })

    fireEvent.doubleClick(queuedCard(/John 1:1/))

    expect(sentActions()).toEqual([{ action: 'approve', reference_id: 'p-1' }])
    expect(screen.getByText('Nothing staged')).toBeInTheDocument()
  })

  it('a confirmed approval frees the next double-click', () => {
    openWith({ currentDisplay: live, pending: [queued, queuedAgain] })

    fireEvent.doubleClick(queuedCard(/John 1:1/))
    push(socket, { type: 'approval_confirmed', data: { reference: queued } })
    fireEvent.doubleClick(queuedCard(/Acts 2:38/))

    expect(sentActions()).toEqual([
      { action: 'approve', reference_id: 'p-1' },
      { action: 'approve', reference_id: 'p-2' },
    ])
  })
})

describe('a double-click leaves Up Next as it was', () => {
  it('a history row double-clicked after another entry was staged is shown once, and the staged entry stays', () => {
    openWith({ currentDisplay: live, entries: history })
    const psalmsCard = historyCard(/Psalms 23:1/)
    fireEvent.click(psalmsCard)
    expect(screen.getByTestId('next-preview')).toHaveTextContent('Psalms 23:1')

    const johnCard = historyCard(/John 3:16/)
    fireEvent.click(johnCard)
    fireEvent.click(johnCard)
    fireEvent.doubleClick(johnCard)

    expect(sentActions()).toEqual([
      { action: 'display_reference', book: 'John', chapter: 3, verse_start: 16, verse_end: 16 },
    ])
    expect(screen.getByTestId('next-preview')).toHaveTextContent('Psalms 23:1')

    push(socket, {
      type: 'display_confirmed',
      data: { reference: entry('m-1', 'John', 3, 16, 'manual') },
    })
    expect(screen.getByTestId('live-verses')).toHaveTextContent('John 3:16 text')
    expect(screen.getByTestId('next-preview')).toHaveTextContent('Psalms 23:1')
  })

  it('a queued detection double-clicked after a history entry was staged is approved once, and the staged entry stays', () => {
    openWith({ currentDisplay: live, pending: [queued], entries: history })
    fireEvent.click(historyCard(/Psalms 23:1/))
    expect(screen.getByTestId('next-preview')).toHaveTextContent('Psalms 23:1')

    fireEvent.click(screen.getByRole('tab', { name: /^Pending/ }))
    const card = queuedCard(/John 1:1/)
    fireEvent.click(card)
    fireEvent.click(card)
    fireEvent.doubleClick(card)

    expect(sentActions()).toEqual([{ action: 'approve', reference_id: 'p-1' }])
    expect(screen.getByTestId('next-preview')).toHaveTextContent('Psalms 23:1')

    push(socket, { type: 'approval_confirmed', data: { reference: queued } })
    expect(screen.getByTestId('next-preview')).toHaveTextContent('Psalms 23:1')
  })
})

describe('double-click while the socket is closed', () => {
  it('sends nothing for a history row or a queued detection', () => {
    openWith({ currentDisplay: live, pending: [queued], entries: history })
    act(() => {
      socket?.onclose?.({ code: 1006 })
    })
    expect(screen.getByText(/Not connected to the server/)).toBeInTheDocument()

    fireEvent.doubleClick(historyCard(/John 3:16/))
    fireEvent.click(screen.getByRole('tab', { name: /^Pending/ }))
    fireEvent.doubleClick(queuedCard(/John 1:1/))

    expect(sentActions()).toEqual([])
  })
})

describe('one in-flight guard for double-clicks and Show', () => {
  it('a double-click on a history entry is ignored while a Show is in flight', () => {
    openWith({ currentDisplay: live, pending: [queued], entries: history })
    const romansCard = historyCard(/Romans 8:28/)
    act(() => {
      romansCard.click()
    })
    show()

    // Back to the queue, where the detection's card is.
    fireEvent.click(screen.getByRole('tab', { name: /^Pending/ }))
    fireEvent.doubleClick(queuedCard(/John 1:1/))

    expect(sentActions()).toHaveLength(1)
    expect(lastSent(socket)).toMatchObject({ action: 'display_reference', book: 'Romans' })
  })
})
