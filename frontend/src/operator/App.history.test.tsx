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

/** The History tab, opened. */
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

/** The history row whose title matches (opening the tab first). */
function historyRow(name: RegExp) {
  const rows = historyPanel()
    .getAllByRole('heading', { level: 3 })
    .filter(heading => name.test(heading.textContent ?? ''))
  expect(rows).toHaveLength(1)
  return rows[0].closest('.ref-item') as HTMLElement
}

/** Double-click the history row whose title matches. */
function showAgain(name: RegExp) {
  fireEvent.doubleClick(historyRow(name))
}

beforeEach(() => {
  window.localStorage.clear()
  vi.stubGlobal('WebSocket', MockWebSocket)
  // jsdom has no matchMedia; ThemeToggle's useTheme hook reads it on mount.
  vi.stubGlobal('matchMedia', (query: string) => ({
    matches: query === '(hover: none) and (pointer: coarse)' && touchLayout,
    media: query,
    onchange: null,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }))
})

let touchLayout = false

afterEach(() => {
  touchLayout = false
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

    const row = within(historyRow(/Romans 8:28/))
    expect(row.getByText('Romans 8:28')).toBeInTheDocument()
    expect(row.getByText('Search')).toBeInTheDocument()
    const time = row.getByText(/\d{1,2}:\d{2}:\d{2}/)
    expect(time).toHaveAttribute('datetime', '2026-01-01T10:00:00Z')

    // Confidence belongs to a detection, not to something shown: no word, no edge.
    const detected = historyRow(/John 3:16/)
    expect(within(detected).queryByText('High')).not.toBeInTheDocument()
    expect(detected).not.toHaveAttribute('data-confidence')
  })

  it('replays from the row and takes no cursor', () => {
    openWith({ history })

    const row = within(historyRow(/John 3:16/))
    expect(row.getByRole('button', { name: 'Show John 3:16 again' })).toBeInTheDocument()
    expect(row.queryByRole('button', { name: /^(Show|Remove)$/ })).not.toBeInTheDocument()
    expect(historyPanel().getByText('Double-click / Enter to show again')).toBeInTheDocument()

    // A click on the row selects nothing and sends nothing.
    fireEvent.click(row.getByText('John 3:16'))
    expect(historyRow(/John 3:16/)).not.toHaveClass('is-selected')
    fireEvent.keyDown(window, { key: 'Enter' })
    expect(sentActions()).toEqual([])
  })

  it('selects one History row on touch and waits for its Show button', () => {
    touchLayout = true
    openWith({ history, currentDisplay: live })

    fireEvent.click(within(historyRow(/Romans 8:28/)).getByText('Romans 8:28'))
    fireEvent.click(within(historyRow(/John 3:16/)).getByText('John 3:16'))

    expect(historyRow(/Romans 8:28/)).not.toHaveClass('is-selected')
    expect(historyRow(/John 3:16/)).toHaveClass('is-selected')
    expect(sentActions()).toEqual([])

    fireEvent.click(historyPanel().getByRole('button', { name: 'Show John 3:16 on display' }))
    expect(sentActions()).toEqual([
      { action: 'display_reference', book: 'John', chapter: 3, verse_start: 16, verse_end: 16 },
    ])
  })

  it('shows an empty state and no Show buttons when nothing has been shown', () => {
    openWith()

    expect(historyPanel().getByText('No history yet')).toBeInTheDocument()
    expect(historyPanel().queryByRole('button', { name: 'Show' })).not.toBeInTheDocument()
  })

  it('clears History after confirmation and keeps the live preview', () => {
    openWith({ history: [john], currentDisplay: john })
    const panel = historyPanel()
    expect(panel.queryByRole('button', { name: 'Pending options' })).not.toBeInTheDocument()
    const options = panel.getByRole('button', { name: 'History options' })
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
    function openMenu() {
      fireEvent.click(options)
      return panel.getByRole('menuitem', { name: 'Clear history' })
    }

    fireEvent.click(openMenu())
    expect(confirm).toHaveBeenCalledWith("Clear history? This can't be undone.")
    expect(sentActions()).toEqual([])

    confirm.mockReturnValue(true)
    fireEvent.click(openMenu())
    expect(sentActions()).toEqual([{ action: 'clear_history' }])
    expect(panel.getByText('John 3:16')).toBeInTheDocument()

    push(socket, { type: 'history_cleared', data: { history: [], history_count: 0 } })
    expect(panel.getByText('No history yet')).toBeInTheDocument()
    expect(within(screen.getByTestId('live-verses')).getByText(john.text)).toBeInTheDocument()
    const disabled = openMenu()
    expect(disabled).toHaveAttribute('aria-disabled', 'true')
    fireEvent.click(disabled)
    expect(confirm).toHaveBeenCalledTimes(2)
    expect(sentActions()).toHaveLength(1)
  })

  it('disables Clear history when disconnected', () => {
    openWith({ history: [john] })
    act(() => socket?.onclose?.({ code: 1006 }))
    const panel = historyPanel()
    fireEvent.click(panel.getByRole('button', { name: 'History options' }))
    expect(panel.getByRole('menuitem', { name: 'Clear history' })).toHaveAttribute(
      'aria-disabled',
      'true'
    )
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

    // The row is the newest entry: its time is the newer of the two.
    expect(within(historyRow(/John 3:16/)).getByText(/\d{1,2}:\d{2}:\d{2}/)).toHaveAttribute(
      'datetime',
      '2026-01-01T11:00:00Z'
    )

    showAgain(/John 3:16/)
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

    showAgain(/Psalms 23:1/)
    push(socket, {
      type: 'display_confirmed',
      data: { reference: entry('m-4', 'Psalms', 23, 1, 'manual') },
    })

    expect(historyTitles()).toEqual(['Psalms 23:1', 'Romans 8:28', 'John 3:16'])
  })
})

describe('Show again', () => {
  it('sends coordinates only and waits for the server to confirm', () => {
    openWith({ currentDisplay: live, history })

    showAgain(/John 3:16/)

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

    push(socket, {
      type: 'display_confirmed',
      data: { reference: entry('m-1', 'John', 3, 16, 'manual') },
    })

    expect(
      within(screen.getByTestId('live-verses')).getByText('John 3:16 text')
    ).toBeInTheDocument()
    expect(screen.queryByText(/^Shown:/)).not.toBeInTheDocument()
  })

  it('plain Enter on the focused History row replays it, once per press', () => {
    openWith({ history })
    const replay = within(historyRow(/John 3:16/)).getByRole('button', {
      name: 'Show John 3:16 again',
    })
    replay.focus()
    fireEvent.keyDown(replay, { key: 'Enter', ctrlKey: true })
    fireEvent.keyDown(replay, { key: 'Enter', repeat: true })
    expect(sentActions()).toEqual([])
    fireEvent.keyDown(replay, { key: 'Enter' })
    expect(sentActions()).toEqual([
      { action: 'display_reference', book: 'John', chapter: 3, verse_start: 16, verse_end: 16 },
    ])
  })

  it('sends a detected single verse as it was detected', () => {
    // A detection that named one verse reaches history with `verse_end: null`; the
    // server resolves that as the one verse.
    const detectedVerse = { ...entry('h-n', 'John', 3, 16), verse_end: null }
    openWith({ currentDisplay: live, history: [detectedVerse] })

    showAgain(/John 3:16/)

    expect(lastSent(socket)).toEqual({
      action: 'display_reference',
      book: 'John',
      chapter: 3,
      verse_start: 16,
      verse_end: null,
    })
  })

  it('ignores a second Show while the first is still in flight', () => {
    openWith({ history })

    showAgain(/John 3:16/)
    showAgain(/Romans 8:28/)

    expect(sentActions()).toHaveLength(1)
  })

  it('reports a rejected Show and lets the operator try again', () => {
    openWith({ currentDisplay: live, history })
    showAgain(/John 3:16/)

    push(socket, {
      type: 'error',
      data: { action: 'display_reference', message: 'John 3:16 is not in the Bible text' },
    })

    expect(
      screen.getByText('Could not show that passage: John 3:16 is not in the Bible text')
    ).toBeInTheDocument()
    expect(
      within(screen.getByTestId('live-verses')).getByText('Luke 2:11 text')
    ).toBeInTheDocument()
    showAgain(/John 3:16/)
    expect(sentActions()).toHaveLength(2)
  })

  it('is disabled while the socket is closed', () => {
    openWith({ history })
    act(() => {
      socket?.onclose?.({ code: 1006 })
    })

    const row = historyRow(/John 3:16/)
    const replay = within(row).getByRole('button', { name: 'Show John 3:16 again' })
    expect(replay).toHaveAttribute('aria-disabled', 'true')
    fireEvent.keyDown(replay, { key: 'Enter' })
    fireEvent.doubleClick(row)
    expect(sentActions()).toEqual([])
  })

  it('works again after a dropped socket reconnects', () => {
    openWith({ currentDisplay: live, history })
    showAgain(/John 3:16/)
    act(() => {
      socket?.onclose?.({ code: 1006 })
    })

    act(() => {
      screen.getByRole('button', { name: 'Reconnect' }).click()
    })
    act(() => {
      socket?.onopen?.()
    })
    openWithSnapshot({ currentDisplay: live, entries: history })
    showAgain(/Romans 8:28/)
    expect(lastSent(socket)).toMatchObject({ action: 'display_reference', book: 'Romans' })
  })
})

describe('switching between the lists', () => {
  it('shows a history entry as coordinates and a queued detection as an approval', () => {
    const pending = [entry('p-1', 'John', 1, 1)]
    openWith({ currentDisplay: live, history, pending })

    showAgain(/Romans 8:28/)
    expect(lastSent(socket)).toEqual({
      action: 'display_reference',
      book: 'Romans',
      chapter: 8,
      verse_start: 28,
      verse_end: 28,
    })
    push(socket, { type: 'display_confirmed', data: { reference: roman } })

    fireEvent.doubleClick(
      pendingPanel().getByRole('button', { name: 'John 1:1 — highlight; Enter shows it' })
    )
    expect(lastSent(socket)).toEqual({ action: 'approve', reference_id: 'p-1' })
  })

  it('never puts a history entry in the pending queue', () => {
    const pending = [entry('p-1', 'John', 1, 1)]
    openWith({ currentDisplay: live, history, pending })
    historyRow(/Romans 8:28/)

    const queued = pendingPanel()
      .getAllByRole('heading', { level: 3 })
      .map(heading => heading.textContent)
    expect(queued).toEqual(['John 1:1'])
    expect(sentActions()).toEqual([])
  })
})
