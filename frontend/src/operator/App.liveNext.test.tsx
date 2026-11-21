import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, within } from '@testing-library/react'
import App from './App'
import type { BibleReference, VerseLimit } from '../shared/types'
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
const acts = reference('p-3', 'Acts', 2, [38])

const queue = [john, romans]

/** Snapshot with the given display state, delivered after the socket opens. */
function openWith(
  options: {
    currentDisplay?: BibleReference | null
    verseLimit?: VerseLimit
    pending?: BibleReference[]
    history?: BibleReference[]
  } = {}
) {
  const { currentDisplay = null, verseLimit = 3, pending = queue, history = [] } = options

  render(<App />)
  act(() => {
    socket?.onopen?.()
  })
  push(socket, {
    type: 'state_snapshot',
    data: {
      current_display: currentDisplay,
      pending,
      history,
      pending_count: pending.length,
      history_count: history.length,
      display_connections: 1,
      operator_connections: 1,
      display_settings: { verse_limit: verseLimit, theme: 'dark' },
    },
  })
}

/** The pending row (its highlight handle) whose reference matches. */
function row(name: RegExp) {
  const rows = screen
    .getAllByRole('button', { name })
    .filter(button => button.getAttribute('aria-label')?.endsWith('— highlight; Enter shows it'))
  expect(rows).toHaveLength(1)
  return rows[0]
}

/** Click a pending row: highlights it. */
function highlight(name: RegExp) {
  fireEvent.click(row(name))
}

function pressKey(key: string) {
  fireEvent.keyDown(window, { key })
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
    // The citation is on the monitor, under the verses, as on the projector.
    expect(screen.getByText('Psalms 23:1-5')).toBeInTheDocument()
    // The only 'Live' is the live switch's button.
    expect(screen.getAllByText('Live').every(el => el.closest('button'))).toBe(true)
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

    push(socket, { type: 'approval_confirmed', data: { reference: psalms } })

    expect(
      within(screen.getByTestId('live-verses')).getByText('Psalms 23:1 text')
    ).toBeInTheDocument()
    expect(screen.queryByText('Nothing on display')).not.toBeInTheDocument()
  })

  it('returns to the empty state when the display is cleared', () => {
    openWith({ currentDisplay: psalms })

    push(socket, { type: 'clear_confirmed', data: {} })

    expect(screen.getByText('Nothing on display')).toBeInTheDocument()
  })

  it('previews the display theme the projectors are using', () => {
    openWith({ currentDisplay: psalms })
    expect(screen.getByTestId('live-stage')).toHaveAttribute('data-theme', 'dark')

    push(socket, { type: 'display_settings_confirmed', data: { verse_limit: 3, theme: 'light' } })

    expect(screen.getByTestId('live-stage')).toHaveAttribute('data-theme', 'light')
  })

  it('has no Up Next card: staging is gone', () => {
    openWith()

    expect(screen.queryByText('Up Next')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Show on display' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Stage/ })).not.toBeInTheDocument()
  })
})

describe('the row cursor in Pending', () => {
  it('shows the navigation hint only while Pending has rows', () => {
    openWith({ pending: [] })
    const panel = within(screen.getByRole('tabpanel'))
    const empty = 'Detected references will appear here.'
    const hint = '↑ ↓ to move · Double-click or Enter to show'
    const options = panel.getByRole('button', { name: 'Pending options' })

    expect(panel.getByText(empty)).toBeInTheDocument()
    expect(panel.queryByText(hint)).not.toBeInTheDocument()
    fireEvent.click(options)
    expect(panel.getByRole('menuitemcheckbox', { name: 'Auto-approve Off' })).toBeEnabled()
    fireEvent.click(options)

    push(socket, { type: 'pending_update', data: { pending: [john], count: 1 } })
    expect(panel.queryByText(empty)).not.toBeInTheDocument()
    expect(panel.getByText(hint)).toBeInTheDocument()

    push(socket, { type: 'pending_update', data: { pending: [], count: 0 } })
    expect(panel.getByText(empty)).toBeInTheDocument()
    expect(panel.queryByText(hint)).not.toBeInTheDocument()
    expect(options).toBeInTheDocument()
  })

  it('a click highlights a row and sends nothing', () => {
    openWith({ currentDisplay: psalms })

    highlight(/Romans 8:28/)

    expect(row(/Romans 8:28/)).toHaveAttribute('aria-pressed', 'true')
    expect(row(/John 1:1/)).toHaveAttribute('aria-pressed', 'false')
    expect(sentActions()).toEqual([])
    // The display is untouched.
    expect(
      within(screen.getByTestId('live-verses')).getByText('Psalms 23:1 text')
    ).toBeInTheDocument()
  })

  it('names each row for what clicking it does', () => {
    openWith()

    expect(
      screen.getByRole('button', { name: 'Romans 8:28 — highlight; Enter shows it' })
    ).toBeInTheDocument()
  })

  it('highlights the selected row without an inline Show button', () => {
    openWith()
    highlight(/Romans 8:28/)

    expect(row(/Romans 8:28/).closest('.ref-item')).toHaveClass('is-selected')
    expect(screen.queryByRole('button', { name: 'Show' })).not.toBeInTheDocument()
  })

  it('Enter shows the highlighted detection, approved by its ID', () => {
    openWith()

    highlight(/Romans 8:28/)
    pressKey('Enter')

    expect(sentActions()).toEqual([{ action: 'approve', reference_id: 'p-2' }])
  })

  it('Enter with nothing highlighted does nothing', () => {
    openWith()

    pressKey('Enter')

    expect(sentActions()).toEqual([])
  })

  it('the arrows move the highlight, starting at the first row, held at the ends', () => {
    openWith({ pending: [john, romans, acts] })

    pressKey('ArrowDown')
    expect(row(/John 1:1/)).toHaveAttribute('aria-pressed', 'true')
    pressKey('ArrowDown')
    pressKey('ArrowDown')
    pressKey('ArrowDown')
    expect(row(/Acts 2:38/)).toHaveAttribute('aria-pressed', 'true')
    pressKey('ArrowUp')
    expect(row(/Romans 8:28/)).toHaveAttribute('aria-pressed', 'true')
    pressKey('Escape')
    expect(row(/Romans 8:28/)).toHaveAttribute('aria-pressed', 'false')
    expect(sentActions()).toEqual([])
  })

  it('a new detection never takes the highlight', () => {
    openWith({ pending: [john] })
    push(socket, { type: 'pending_update', data: { pending: [acts, john], count: 2 } })

    // Nothing was highlighted, and nothing is now.
    expect(row(/Acts 2:38/)).toHaveAttribute('aria-pressed', 'false')
    pressKey('Enter')
    expect(sentActions()).toEqual([])

    highlight(/John 1:1/)
    push(socket, { type: 'pending_update', data: { pending: [romans, acts, john], count: 3 } })
    expect(row(/John 1:1/)).toHaveAttribute('aria-pressed', 'true')
    expect(row(/Romans 8:28/)).toHaveAttribute('aria-pressed', 'false')
    pressKey('Enter')
    expect(lastSent(socket)).toEqual({ action: 'approve', reference_id: 'p-1' })
  })

  it('approves only the row that received both clicks', () => {
    openWith()

    fireEvent.click(row(/John 1:1/), { detail: 1 })
    expect(sentActions()).toEqual([])
    fireEvent.click(row(/John 1:1/), { detail: 2 })
    fireEvent.doubleClick(row(/John 1:1/))
    expect(sentActions()).toEqual([{ action: 'approve', reference_id: 'p-1' }])
    push(socket, { type: 'approval_confirmed', data: { reference: john } })
    push(socket, { type: 'pending_update', data: { pending: [romans], count: 1 } })

    fireEvent.click(row(/Romans 8:28/), { detail: 2 })
    expect(sentActions()).toEqual([{ action: 'approve', reference_id: 'p-1' }])
  })

  it('drops the highlight when its detection leaves the queue', () => {
    openWith()
    highlight(/Romans 8:28/)

    push(socket, { type: 'pending_update', data: { pending: [john], count: 1 } })
    pressKey('Enter')

    expect(sentActions()).toEqual([])
    expect(document.querySelector('.ref-item.is-selected')).toBeNull()
  })

  it('Enter does nothing while the History tab hides the highlighted row', () => {
    openWith()
    highlight(/Romans 8:28/)

    fireEvent.click(screen.getByRole('tab', { name: 'History' }))
    pressKey('Enter')
    pressKey('ArrowDown')
    expect(sentActions()).toEqual([])

    fireEvent.click(screen.getByRole('tab', { name: /Pending/ }))
    expect(row(/Romans 8:28/)).toHaveAttribute('aria-pressed', 'true')
    pressKey('Enter')
    expect(sentActions()).toEqual([{ action: 'approve', reference_id: 'p-2' }])
  })

  it('focus on a row highlights it, so Enter shows the focused row', () => {
    openWith()

    act(() => row(/John 1:1/).focus())
    fireEvent.keyDown(row(/John 1:1/), { key: 'Enter' })

    expect(sentActions()).toEqual([{ action: 'approve', reference_id: 'p-1' }])
  })

  it('after an arrow moves the highlight past the first row, that row has focus', () => {
    const scroll = vi.mocked(Element.prototype.scrollIntoView)
    scroll.mockClear()
    openWith()

    pressKey('ArrowDown')
    expect(row(/John 1:1/)).toHaveFocus()
    pressKey('ArrowDown')

    expect(row(/Romans 8:28/)).toHaveAttribute('aria-pressed', 'true')
    expect(row(/Romans 8:28/)).toHaveFocus()
    expect(scroll).toHaveBeenLastCalledWith({ block: 'nearest' })
    // Focus and the cursor stay together, so Enter shows the row that has focus.
    pressKey('Enter')
    expect(sentActions()).toEqual([{ action: 'approve', reference_id: 'p-2' }])
  })

  it('a server update does not move focus or the highlight', () => {
    openWith()
    pressKey('ArrowDown')
    pressKey('ArrowDown')

    push(socket, { type: 'pending_update', data: { pending: [acts, john, romans], count: 3 } })

    expect(row(/Romans 8:28/)).toHaveFocus()
    expect(row(/Romans 8:28/)).toHaveAttribute('aria-pressed', 'true')
    expect(row(/Acts 2:38/)).toHaveAttribute('aria-pressed', 'false')
  })

  it('double-click approves the clicked row, highlighted or not', () => {
    openWith()

    fireEvent.doubleClick(row(/John 1:1/))

    expect(sentActions()).toEqual([{ action: 'approve', reference_id: 'p-1' }])
  })

  it('shows the approved reference once the server confirms it', () => {
    openWith()

    highlight(/Romans 8:28/)
    pressKey('Enter')
    push(socket, { type: 'approval_confirmed', data: { reference: romans } })
    push(socket, { type: 'pending_update', data: { pending: [john], count: 1 } })

    expect(
      within(screen.getByTestId('live-verses')).getByText('Romans 8:28 text')
    ).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Romans 8:28/ })).not.toBeInTheDocument()
  })

  it('a second Show while the first is in flight sends nothing more', () => {
    openWith()

    fireEvent.doubleClick(row(/John 1:1/))
    fireEvent.doubleClick(row(/Romans 8:28/))
    highlight(/Romans 8:28/)
    pressKey('Enter')
    expect(sentActions()).toEqual([{ action: 'approve', reference_id: 'p-1' }])

    // The confirmation frees the next Show.
    push(socket, { type: 'approval_confirmed', data: { reference: john } })
    fireEvent.doubleClick(row(/Romans 8:28/))
    expect(lastSent(socket)).toEqual({ action: 'approve', reference_id: 'p-2' })
  })

  it('Remove is an icon button named with its reference, and removes by ID', () => {
    openWith()

    fireEvent.click(screen.getByRole('button', { name: 'Remove Romans 8:28 from the queue' }))

    expect(sentActions()).toEqual([{ action: 'discard_pending', reference_id: 'p-2' }])
  })

  it('puts only Remove beside the timestamp', () => {
    openWith()

    const actions = row(/Romans 8:28/)
      .closest('.ref-item')!
      .querySelector<HTMLElement>('.ref-item-foot')!
    const buttons = within(actions).getAllByRole('button')
    expect(buttons.map(button => button.getAttribute('aria-label') ?? button.textContent)).toEqual([
      'Remove Romans 8:28 from the queue',
    ])
    expect(actions.querySelector('time')?.nextElementSibling).toBe(buttons[0])
  })

  it('disables Show while the socket is not open', () => {
    render(<App />)
    push(socket, {
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
    highlight(/Romans 8:28/)

    fireEvent.doubleClick(row(/Romans 8:28/))
    pressKey('Enter')
    expect(sentActions()).toEqual([])
  })
})
