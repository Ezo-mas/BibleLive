import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, within } from '@testing-library/react'
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

/** The newest history entry first, exactly as the server sends the window. */
const psalms = entry('h-1', 'Psalms', 23, 1)
const john = entry('h-2', 'John', 3, 16)
const romans = entry('h-3', 'Romans', 8, 28, 'manual')
const history = [romans, john, psalms]

/** What the projector is showing while the operator browses history. */
const live = entry('d-9', 'Luke', 2, 11)

/** The server-built reference that answers a display request for Romans 8:28. */
const roman = entry('m-2', 'Romans', 8, 28, 'manual')

function openWith(
  options: {
    currentDisplay?: BibleReference | null
    history?: BibleReference[]
    historyCount?: number
    pending?: BibleReference[]
  } = {}
) {
  const { currentDisplay = null, history: entries = [], historyCount, pending = [] } = options

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
      history_count: historyCount ?? entries.length,
      display_connections: 1,
      operator_connections: 1,
      display_settings: { verse_limit: 3, theme: 'dark' },
    },
  })
}

/** A second snapshot on the open socket, for reconnects and history updates. */
function openWithSnapshot({
  currentDisplay = null,
  entries = [] as BibleReference[],
  historyCount,
}: {
  currentDisplay?: BibleReference | null
  entries?: BibleReference[]
  historyCount?: number
}) {
  push(socket, {
    type: 'state_snapshot',
    data: {
      current_display: currentDisplay,
      pending: [],
      history: entries,
      pending_count: 0,
      history_count: historyCount ?? entries.length,
      display_connections: 1,
      operator_connections: 1,
      display_settings: { verse_limit: 3, theme: 'dark' },
    },
  })
}

/** Open a tab of the sidebar, the way the operator does. */
function openTab(name: string | RegExp) {
  fireEvent.click(screen.getByRole('tab', { name }))
}

/** The history tab, opened: its list is the only body in the tab panel. */
function historyPanel() {
  openTab('History')
  return within(screen.getByRole('tabpanel'))
}

/** The pending tab, opened, for the tests that switch between the two lists. */
function pendingPanel() {
  openTab(/^Pending/)
  return within(screen.getByRole('tabpanel'))
}

/** The history entries' headings, top to bottom. */
function historyTitles() {
  return historyPanel()
    .getAllByRole('heading', { level: 3 })
    .map(heading => heading.textContent)
}

/** The selectable history card whose reference matches. */
function historyCard(name: RegExp) {
  return historyPanel().getByRole('button', { name })
}

function selectHistory(name: RegExp) {
  // The card is found (which opens the tab) before the click, so the tab switch is
  // not left queued inside the act that delivers the click.
  const card = historyCard(name)
  act(() => {
    card.click()
  })
}

function show() {
  act(() => {
    screen.getByRole('button', { name: 'Show on display' }).click()
  })
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

describe('history list', () => {
  it('lists the entries newest first, and the tab shows no count', () => {
    openWith({ history, historyCount: 42 })

    expect(historyTitles()).toEqual(['Romans 8:28', 'John 3:16', 'Psalms 23:1'])
    // The list is a place to look back, not a counter: the server's total is not shown.
    expect(
      screen.getByRole('tab', { name: 'History' }).querySelector('.operator-section-count')
    ).toBeNull()
    expect(historyPanel().queryByText('42')).not.toBeInTheDocument()
  })

  it('identifies an entry by reference, time, and source where the data says so', () => {
    openWith({ history })

    const card = historyCard(/Romans 8:28/)
    expect(within(card).getByText('Romans 8:28')).toBeInTheDocument()
    expect(within(card).getByText('Manual')).toBeInTheDocument()
    const time = within(card).getByText(/\d{1,2}:\d{2}:\d{2}/)
    expect(time).toHaveAttribute('datetime', '2026-01-01T10:00:00Z')

    // A detected entry keeps its confidence badge instead.
    expect(within(historyCard(/John 3:16/)).getByText('High')).toBeInTheDocument()
  })

  it('shows an empty state and no selectable cards when nothing has been shown', () => {
    openWith()

    expect(historyPanel().getByText('No history yet')).toBeInTheDocument()
    expect(historyPanel().queryAllByRole('button')).toHaveLength(0)
  })

  it('keeps the server window of twenty entries as confirmations arrive', () => {
    openWith()

    for (let verse = 1; verse <= 21; verse += 1) {
      push(socket, {
        type: 'approval_confirmed',
        data: { reference: entry(`v-${verse}`, 'John', 3, verse) },
      })
    }

    const cards = historyPanel().getAllByRole('heading', { level: 3 })
    expect(cards).toHaveLength(20)
    expect(historyPanel().queryByText('John 3:1')).not.toBeInTheDocument()
    // The tab carries no count, so the twenty-entry window is all that is listed.
    expect(
      screen.getByRole('tab', { name: 'History' }).querySelector('.operator-section-count')
    ).toBeNull()
  })
})

describe('one row per passage', () => {
  it('collapses duplicates into one row, the newest', () => {
    const newest = { ...entry('h-a', 'John', 3, 16), created_at: '2026-01-01T11:00:00Z' }
    const older = { ...entry('h-b', 'John', 3, 16), created_at: '2026-01-01T10:00:00Z' }
    openWith({ currentDisplay: live, history: [newest, older] })

    const rows = historyPanel().getAllByRole('button', { name: /John 3:16/ })
    expect(rows).toHaveLength(1)
    // The row is the newest entry: its time is the newer of the two.
    expect(within(rows[0]).getByText(/\d{1,2}:\d{2}:\d{2}/)).toHaveAttribute(
      'datetime',
      '2026-01-01T11:00:00Z'
    )

    selectHistory(/John 3:16/)
    show()
    expect(lastSent(socket)).toEqual({
      action: 'display_reference',
      book: 'John',
      chapter: 3,
      verse_start: 16,
      verse_end: 16,
    })
  })

  it('moves a passage shown again to the top without adding a row', () => {
    openWith({ currentDisplay: live, history })

    selectHistory(/Psalms 23:1/)
    show()
    push(socket, {
      type: 'display_confirmed',
      data: { reference: entry('m-4', 'Psalms', 23, 1, 'manual') },
    })

    expect(historyTitles()).toEqual(['Psalms 23:1', 'Romans 8:28', 'John 3:16'])
  })

  it('keeps a staged entry marked after a newer entry for its passage replaces its row', () => {
    openWith({ currentDisplay: live, history })
    selectHistory(/John 3:16/)

    // Another detection of the same verse is approved: its row replaces the staged one.
    push(socket, { type: 'approval_confirmed', data: { reference: entry('h-new', 'John', 3, 16) } })

    const card = historyCard(/John 3:16/)
    expect(card).toHaveAttribute('aria-pressed', 'true')
    expect(within(card).getByText('Staged in Up Next')).toBeInTheDocument()
    expect(historyPanel().getAllByRole('button', { name: /John 3:16/ })).toHaveLength(1)
    expect(screen.getByTestId('next-preview')).toHaveTextContent('John 3:16')
    expect(screen.getByText('History', { selector: '.operator-next-badge' })).toBeInTheDocument()

    show()
    expect(lastSent(socket)).toEqual({
      action: 'display_reference',
      book: 'John',
      chapter: 3,
      verse_start: 16,
      verse_end: 16,
    })
  })
})

describe('staging a history entry', () => {
  it('stages the selected entry in Up Next without touching LIVE or the server', () => {
    openWith({ currentDisplay: live, history })

    selectHistory(/John 3:16/)

    expect(screen.getByTestId('next-preview')).toHaveTextContent('John 3:16')
    expect(
      within(screen.getByTestId('next-verses')).getByText('John 3:16 text')
    ).toBeInTheDocument()
    expect(screen.getByText('History', { selector: '.operator-next-badge' })).toBeInTheDocument()
    expect(
      screen.getByText('Staged: John 3:16 — press Show to display it again')
    ).toBeInTheDocument()
    expect(historyCard(/John 3:16/)).toHaveAttribute('aria-pressed', 'true')

    // LIVE is what the projector is showing, and staging is client-side only.
    expect(
      within(screen.getByTestId('live-verses')).getByText('Luke 2:11 text')
    ).toBeInTheDocument()
    expect(screen.queryByText(/^Shown:/)).not.toBeInTheDocument()
    expect(sentActions()).toEqual([])
  })

  it('discards the staged entry without touching LIVE', () => {
    openWith({ currentDisplay: live, history })
    selectHistory(/John 3:16/)

    act(() => {
      screen.getByRole('button', { name: 'Unstage' }).click()
    })

    expect(screen.getByText('Nothing staged')).toBeInTheDocument()
    expect(
      within(screen.getByTestId('live-verses')).getByText('Luke 2:11 text')
    ).toBeInTheDocument()
    expect(historyCard(/John 3:16/)).toHaveAttribute('aria-pressed', 'false')
    expect(sentActions()).toEqual([])
  })
})

describe('Show again', () => {
  it('sends coordinates only and waits for the server to confirm', () => {
    openWith({ currentDisplay: live, history })
    selectHistory(/John 3:16/)

    show()

    expect(lastSent(socket)).toEqual({
      action: 'display_reference',
      book: 'John',
      chapter: 3,
      verse_start: 16,
      verse_end: 16,
    })
    // Sent is not shown: LIVE still holds what the projector had, and nothing is
    // announced as done before the server answers.
    expect(
      within(screen.getByTestId('live-verses')).getByText('Luke 2:11 text')
    ).toBeInTheDocument()
    expect(screen.queryByText(/^Shown:/)).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Show on display' })).toBeDisabled()

    push(socket, {
      type: 'display_confirmed',
      data: { reference: entry('m-1', 'John', 3, 16, 'manual') },
    })

    expect(
      within(screen.getByTestId('live-verses')).getByText('John 3:16 text')
    ).toBeInTheDocument()
    expect(screen.getByText('Nothing staged')).toBeInTheDocument()
    expect(screen.getByText('Shown: John 3:16 (manual)')).toBeInTheDocument()
  })

  it('clears a staged entry the server confirms as the same single verse', () => {
    // A detection that named one verse reaches history with `verse_end: null`,
    // while the server states that passage as `verse_start === verse_end` when it
    // resolves a display request. Both name the same verse, so the confirmation
    // has to clear the staging it belongs to.
    const detectedVerse = { ...entry('h-n', 'John', 3, 16), verse_end: null }
    openWith({ currentDisplay: live, history: [detectedVerse] })
    selectHistory(/John 3:16/)

    show()
    expect(lastSent(socket)).toEqual({
      action: 'display_reference',
      book: 'John',
      chapter: 3,
      verse_start: 16,
      verse_end: null,
    })

    push(socket, {
      type: 'display_confirmed',
      data: { reference: entry('m-3', 'John', 3, 16, 'manual') },
    })

    expect(screen.getByText('Nothing staged')).toBeInTheDocument()
    expect(
      within(screen.getByTestId('live-verses')).getByText('John 3:16 text')
    ).toBeInTheDocument()
  })

  it('ignores a second Show while the first is still in flight', () => {
    openWith({ history })
    selectHistory(/John 3:16/)

    show()
    show()

    expect(sentActions()).toHaveLength(1)
  })

  it('reports a rejected Show and leaves the display and the staged entry alone', () => {
    openWith({ currentDisplay: live, history })
    selectHistory(/John 3:16/)
    show()

    push(socket, {
      type: 'error',
      data: { action: 'display_reference', message: 'John 3:16 is not in the Bible text' },
    })

    expect(screen.getByRole('alert')).toHaveTextContent(
      'Could not show that passage: John 3:16 is not in the Bible text'
    )
    expect(
      within(screen.getByTestId('live-verses')).getByText('Luke 2:11 text')
    ).toBeInTheDocument()
    expect(screen.queryByText(/^Shown:/)).not.toBeInTheDocument()
    // Still staged, so the operator can retry or discard it deliberately.
    expect(screen.getByTestId('next-preview')).toHaveTextContent('John 3:16')
    expect(screen.getByRole('button', { name: 'Show on display' })).toBeEnabled()
  })

  it('does not clear a different entry staged while the request was in flight', () => {
    openWith({ currentDisplay: live, history })
    selectHistory(/John 3:16/)
    show()

    // The operator keeps working: a second entry is staged before the answer to
    // the first request arrives.
    selectHistory(/Romans 8:28/)

    push(socket, {
      type: 'display_confirmed',
      data: { reference: entry('m-1', 'John', 3, 16, 'manual') },
    })

    expect(
      within(screen.getByTestId('live-verses')).getByText('John 3:16 text')
    ).toBeInTheDocument()
    expect(screen.getByText('Shown: John 3:16 (manual)')).toBeInTheDocument()
    // The confirmation was about John 3:16, so Romans 8:28 is still staged.
    expect(screen.getByTestId('next-preview')).toHaveTextContent('Romans 8:28')
  })

  it('keeps a staged entry that the next snapshot no longer lists', () => {
    openWith({ currentDisplay: live, history })
    selectHistory(/John 3:16/)

    // A reconnect or a long service: the entry ages out of the server's window.
    openWithSnapshot({ currentDisplay: live, entries: [romans] })

    expect(screen.getByTestId('next-preview')).toHaveTextContent('John 3:16')
    expect(screen.getByText('History', { selector: '.operator-next-badge' })).toBeInTheDocument()
    expect(
      within(screen.getByTestId('live-verses')).getByText('Luke 2:11 text')
    ).toBeInTheDocument()
    expect(historyPanel().queryByRole('button', { name: /John 3:16/ })).not.toBeInTheDocument()

    // Show re-resolves the coordinates on the server, so ageing out changes nothing.
    show()
    expect(lastSent(socket)).toEqual({
      action: 'display_reference',
      book: 'John',
      chapter: 3,
      verse_start: 16,
      verse_end: 16,
    })
  })

  it('keeps the staged entry across a dropped socket and re-enables Show on reconnect', () => {
    openWith({ currentDisplay: live, history })
    selectHistory(/John 3:16/)

    act(() => {
      socket?.onclose?.({ code: 1006 })
    })

    expect(screen.getByText(/Not connected to the server/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Show on display' })).toBeDisabled()
    expect(screen.getByTestId('next-preview')).toHaveTextContent('John 3:16')

    act(() => {
      screen.getByRole('button', { name: 'Reconnect' }).click()
    })
    act(() => {
      socket?.onopen?.()
    })
    openWithSnapshot({ currentDisplay: live, entries: history })

    expect(screen.getByRole('button', { name: 'Show on display' })).toBeEnabled()
    expect(screen.getByTestId('next-preview')).toHaveTextContent('John 3:16')
  })
})

describe('switching between the lists', () => {
  it('shows a history entry as coordinates and a queued detection as an approval', () => {
    const pending = [entry('p-1', 'John', 1, 1)]
    openWith({ currentDisplay: live, history, pending })

    selectHistory(/Romans 8:28/)
    show()
    expect(lastSent(socket)).toEqual({
      action: 'display_reference',
      book: 'Romans',
      chapter: 8,
      verse_start: 28,
      verse_end: 28,
    })
    push(socket, { type: 'display_confirmed', data: { reference: roman } })
    expect(screen.getByText('Nothing staged')).toBeInTheDocument()

    const queuedCard = pendingPanel().getByRole('button', { name: /John 1:1/ })
    act(() => {
      queuedCard.click()
    })
    expect(screen.getByText('Detected')).toBeInTheDocument()
    show()
    expect(lastSent(socket)).toEqual({ action: 'approve', reference_id: 'p-1' })
  })

  it('never puts a history entry in the pending queue', () => {
    const pending = [entry('p-1', 'John', 1, 1)]
    openWith({ currentDisplay: live, history, pending })
    selectHistory(/Romans 8:28/)

    // The queue still holds exactly its own detection: a history entry is never
    // queued, so it has nothing to approve and nothing to clear.
    const queued = pendingPanel()
      .getAllByRole('heading', { level: 3 })
      .map(heading => heading.textContent)
    expect(queued).toEqual(['John 1:1'])
    // It appears in Up Next and as a history entry, and nowhere else.
    expect(screen.getByTestId('next-preview')).toHaveTextContent('Romans 8:28')
    expect(historyCard(/Romans 8:28/)).toBeInTheDocument()
    expect(sentActions()).toEqual([])
  })
})
