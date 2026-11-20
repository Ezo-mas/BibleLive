import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import App from './App'
import { PLAN_STORAGE_KEY } from './hooks/usePlan'
import type { PlanItem } from './hooks/usePlan'
import type { BibleReference } from '../shared/types'
import { createMockSocket, jsonResponse, lastSent, push } from '../test/operatorHarness'
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

const BOOKS = {
  books: [
    { name: 'Genesis', chapters: 50, order: 1, abbrev: 'gn', variants: ['genesis'] },
    { name: 'Psalms', chapters: 150, order: 19, abbrev: 'ps', variants: ['psalms', 'psalm'] },
    { name: 'John', chapters: 21, order: 43, abbrev: 'jn', variants: ['john'] },
    { name: 'Romans', chapters: 16, order: 45, abbrev: 'rm', variants: ['romans'] },
  ],
}

/** A passage the server would resolve for the lookup endpoint. */
function passageBody(url: string) {
  const params = new URLSearchParams(url.split('?')[1] ?? '')
  const book = params.get('book') ?? ''
  const chapter = Number(params.get('chapter'))
  const start = Number(params.get('verse_start') ?? '1')
  const end = Number(params.get('verse_end') ?? String(start))
  const verses = []
  for (let verse = start; verse <= end; verse += 1) {
    verses.push({ verse, text: `${book} ${chapter}:${verse} text` })
  }
  return {
    book,
    chapter,
    verse_start: start,
    verse_end: end,
    verses,
    text: verses.map(v => v.text).join(' '),
  }
}

/**
 * While this is set, lookups wait until the test releases them, so a lookup can
 * be made to land after the operator has moved on.
 */
let deferLookups = false
let releases: Array<() => void> = []
/** When set, every lookup is refused with this reason. */
let lookupFailure: string | null = null

/** The console's reads of the server, answered the way the server would. */
function createFetchMock() {
  return vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    if (url.startsWith('/api/language')) return jsonResponse({ language: 'en' })
    if (url.startsWith('/api/bible-version')) {
      return jsonResponse({ version: 'kjv', available: ['kjv'] })
    }
    if (url.startsWith('/api/pipeline/')) {
      return jsonResponse({
        status: 'unknown',
        language: null,
        problems: [],
        seconds_since_report: null,
      })
    }
    if (url.startsWith('/api/books')) return jsonResponse(BOOKS)
    if (lookupFailure !== null) return jsonResponse({ detail: lookupFailure }, 404)
    if (!deferLookups) return jsonResponse(passageBody(url))
    return new Promise<Response>(resolve => {
      releases.push(() => resolve(jsonResponse(passageBody(url))))
    })
  })
}

let fetchMock: ReturnType<typeof createFetchMock>

/** Every /api/lookup request made so far. */
function lookupCalls() {
  return fetchMock.mock.calls.filter(([url]) => String(url).startsWith('/api/lookup'))
}

/** Let every deferred lookup answer, and the answers settle. */
async function releaseLookups() {
  const pending = releases
  releases = []
  await act(async () => {
    pending.forEach(release => release())
    await new Promise(resolve => setTimeout(resolve, 0))
  })
}

/** A plan item as the console keeps it in storage. */
function planItem(
  id: string,
  book: string,
  chapter: number,
  verseStart: number,
  verseEnd = verseStart
): PlanItem {
  return {
    id,
    book,
    book_label: null,
    chapter,
    verse_start: verseStart,
    verse_end: verseEnd,
    snippet: `${book} ${chapter}:${verseStart} text`,
    snippetFormat: 'passage',
  }
}

const seeded = [
  planItem('a', 'John', 3, 16),
  planItem('b', 'Romans', 8, 28),
  planItem('c', 'Psalms', 23, 1, 6),
]

function seedPlan(items: PlanItem[]) {
  window.localStorage.setItem(PLAN_STORAGE_KEY, JSON.stringify(items))
}

/** The plan ids as the console stored them last. */
function storedIds() {
  const items = JSON.parse(window.localStorage.getItem(PLAN_STORAGE_KEY) ?? '[]') as PlanItem[]
  return items.map(item => item.id)
}

/** Connect the console and deliver a snapshot. */
function openWith({ currentDisplay = null }: { currentDisplay?: BibleReference | null } = {}) {
  const view = render(<App />)
  act(() => {
    socket?.onopen?.()
  })
  push(socket, {
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
  return view
}

/** The Plan card: always on the page, under ON DISPLAY. */
function planPanel() {
  return within(screen.getByRole('region', { name: 'Plan' }))
}

/** The plan rows' titles, top to bottom. */
function planTitles() {
  return planPanel()
    .queryAllByRole('listitem')
    .map(row => row.querySelector('.plan-row-title')?.textContent)
}

/** The number each plan row shows, top to bottom. */
function planNumbers() {
  return planPanel()
    .queryAllByRole('listitem')
    .map(row => row.querySelector('.plan-row-number')?.textContent)
}

/** A plan row's handle: a click (or focus) highlights it. */
function rowHandle(label: string) {
  return screen.getByRole('button', { name: `${label} — highlight; Enter shows it` })
}

/** The plan row for a passage, found by its title. */
function planRow(label: string) {
  const row = planPanel()
    .getAllByRole('listitem')
    .find(item => item.querySelector('.plan-row-title')?.textContent === label)
  if (!row) throw new Error(`no plan row for ${label}`)
  return row
}

function pressKey(key: string) {
  fireEvent.keyDown(window, { key })
}

function searchBox() {
  return screen.getByLabelText('Find scripture by book or reference')
}

/** Type into the search box (the results arrive after the debounce). */
function type(text: string) {
  fireEvent.change(searchBox(), { target: { value: text } })
}

/** Ctrl+Enter (or another modifier) in the search box: add the best match to the plan. */
function pressPlanKey(modifier: 'ctrlKey' | 'metaKey' | 'shiftKey' = 'ctrlKey') {
  act(() => {
    fireEvent.keyDown(searchBox(), { key: 'Enter', [modifier]: true })
  })
}

function manualReference(book: string, chapter: number, verse: number): BibleReference {
  return {
    id: 'manual-1',
    book,
    chapter,
    verse_start: verse,
    verse_end: verse,
    confidence: null,
    inferred: false,
    source: 'manual',
    text: `${book} ${chapter}:${verse} text`,
    verses: [{ verse, text: `${book} ${chapter}:${verse} text` }],
    created_at: '2026-01-01T00:00:00Z',
  }
}

beforeEach(() => {
  window.localStorage.clear()
  deferLookups = false
  releases = []
  lookupFailure = null
  vi.stubGlobal('WebSocket', MockWebSocket)
  fetchMock = createFetchMock()
  vi.stubGlobal('fetch', fetchMock)
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

describe('the plan card', () => {
  it('lists the planned passages in order, numbered, always in view', () => {
    seedPlan(seeded)
    openWith()

    expect(planTitles()).toEqual(['John 3:16', 'Romans 8:28', 'Psalms 23:1-6'])
    expect(planNumbers()).toEqual(['1', '2', '3'])
    // Plan is not a tab any more; the side panel has Pending and History only.
    expect(screen.getAllByRole('tab').map(tab => tab.textContent)).toEqual([
      expect.stringMatching(/^Pending/),
      'History',
    ])
    // Still in view with the History tab open.
    fireEvent.click(screen.getByRole('tab', { name: 'History' }))
    expect(planTitles()).toHaveLength(3)
  })

  it('counts its passages in its head', () => {
    seedPlan(seeded)
    openWith()

    const region = screen.getByRole('region', { name: 'Plan' })
    expect(region.querySelector('.operator-card-head .operator-section-count')).toHaveTextContent(
      '3'
    )
  })

  it('says so when nothing is planned, and offers no Clear', () => {
    openWith()

    expect(screen.getByText('Nothing planned')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Plan options' }))
    expect(screen.getByRole('menuitem', { name: 'Clear plan' })).toHaveAttribute(
      'aria-disabled',
      'true'
    )
  })

  it('moves an item up or down, and the numbers and the stored order follow', () => {
    seedPlan(seeded)
    openWith()

    fireEvent.click(screen.getByRole('button', { name: 'Move Romans 8:28 up' }))

    expect(planTitles()).toEqual(['Romans 8:28', 'John 3:16', 'Psalms 23:1-6'])
    expect(planNumbers()).toEqual(['1', '2', '3'])
    expect(screen.getByRole('button', { name: 'Move Romans 8:28 up' })).toBeDisabled()
    expect(storedIds()).toEqual(['b', 'a', 'c'])

    fireEvent.click(screen.getByRole('button', { name: 'Move Romans 8:28 down' }))

    expect(planTitles()).toEqual(['John 3:16', 'Romans 8:28', 'Psalms 23:1-6'])
    expect(screen.getByRole('button', { name: 'Move Psalms 23:1-6 down' })).toBeDisabled()
    expect(storedIds()).toEqual(['a', 'b', 'c'])
  })

  it('removes an item and keeps the others in order', () => {
    seedPlan(seeded)
    openWith()

    fireEvent.click(screen.getByRole('button', { name: 'Remove Romans 8:28 from the plan' }))

    expect(planTitles()).toEqual(['John 3:16', 'Psalms 23:1-6'])
    expect(planNumbers()).toEqual(['1', '2'])
    expect(storedIds()).toEqual(['a', 'c'])
    expect(sentActions()).toEqual([])
  })

  it('clears the plan only once the operator confirms', () => {
    seedPlan(seeded)
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
    openWith()

    fireEvent.click(screen.getByRole('button', { name: 'Plan options' }))
    fireEvent.click(screen.getByRole('menuitem', { name: 'Clear plan' }))
    expect(confirm).toHaveBeenCalledWith('Remove every passage from the plan?')
    expect(planTitles()).toHaveLength(3)

    confirm.mockReturnValue(true)
    fireEvent.click(screen.getByRole('button', { name: 'Plan options' }))
    fireEvent.click(screen.getByRole('menuitem', { name: 'Clear plan' }))

    expect(screen.getByText('Nothing planned')).toBeInTheDocument()
    expect(storedIds()).toEqual([])
  })

  it('keeps the plan and its order across a reload', () => {
    seedPlan(seeded)
    const first = openWith()
    fireEvent.click(screen.getByRole('button', { name: 'Move Romans 8:28 up' }))
    first.unmount()

    openWith()

    expect(planTitles()).toEqual(['Romans 8:28', 'John 3:16', 'Psalms 23:1-6'])
  })
})

describe('the cursor in the plan', () => {
  it('a click highlights the row, and sends and looks up nothing', () => {
    seedPlan(seeded)
    openWith()

    fireEvent.click(rowHandle('Romans 8:28'))

    expect(rowHandle('Romans 8:28')).toHaveAttribute('aria-pressed', 'true')
    expect(planRow('Romans 8:28')).toHaveClass('is-selected')
    expect(sentActions()).toEqual([])
    expect(lookupCalls()).toEqual([])
  })

  it('a click on the row text highlights it too; a click on its buttons does not', () => {
    seedPlan(seeded)
    openWith()

    fireEvent.click(within(planRow('John 3:16')).getByText('John 3:16 text'))
    expect(rowHandle('John 3:16')).toHaveAttribute('aria-pressed', 'true')

    fireEvent.click(screen.getByRole('button', { name: 'Move Romans 8:28 up' }))
    expect(rowHandle('John 3:16')).toHaveAttribute('aria-pressed', 'true')
    expect(rowHandle('Romans 8:28')).toHaveAttribute('aria-pressed', 'false')
  })

  it('Enter shows the highlighted passage as coordinates, with no lookup', () => {
    seedPlan(seeded)
    openWith()

    fireEvent.click(rowHandle('Psalms 23:1-6'))
    pressKey('Enter')

    expect(sentActions()).toEqual([
      { action: 'display_reference', book: 'Psalms', chapter: 23, verse_start: 1, verse_end: 6 },
    ])
    expect(lookupCalls()).toEqual([])
    // A verse is often read twice, so a shown item stays in the plan, still highlighted.
    expect(planTitles()).toHaveLength(3)
    expect(rowHandle('Psalms 23:1-6')).toHaveAttribute('aria-pressed', 'true')
  })

  it('keeps Show compact on a highlighted Plan row', () => {
    seedPlan(seeded)
    openWith()

    fireEvent.click(rowHandle('John 3:16'))

    const show = within(planRow('John 3:16')).getByRole('button', { name: 'Show' })
    expect(show).toHaveClass('btn-primary')
    expect(show).toHaveTextContent(/^Show$/)

    pressKey('Escape')
    expect(show).toHaveClass('btn-secondary')
    expect(show).toHaveTextContent(/^Show$/)
  })

  it('the arrows move through the plan once the cursor is there', () => {
    seedPlan(seeded)
    openWith()

    fireEvent.click(rowHandle('John 3:16'))
    pressKey('ArrowDown')
    expect(rowHandle('Romans 8:28')).toHaveAttribute('aria-pressed', 'true')
    pressKey('ArrowDown')
    pressKey('ArrowDown')
    expect(rowHandle('Psalms 23:1-6')).toHaveAttribute('aria-pressed', 'true')
    pressKey('ArrowUp')
    pressKey('Enter')

    expect(sentActions()).toEqual([
      { action: 'display_reference', book: 'Romans', chapter: 8, verse_start: 28, verse_end: 28 },
    ])
  })

  it('with an empty queue the arrows start in the plan', () => {
    seedPlan(seeded)
    openWith()

    pressKey('ArrowDown')

    expect(rowHandle('John 3:16')).toHaveAttribute('aria-pressed', 'true')
  })

  it('after an arrow move, the highlighted plan row has focus', () => {
    seedPlan(seeded)
    openWith()

    fireEvent.click(rowHandle('John 3:16'))
    pressKey('ArrowDown')

    expect(rowHandle('Romans 8:28')).toHaveFocus()
    // A server update leaves the focus where the operator put it.
    push(socket, { type: 'display_confirmed', data: { reference: manualReference('John', 3, 16) } })
    expect(rowHandle('Romans 8:28')).toHaveFocus()
  })

  it('Enter works from the plan while the History tab is open: the plan is always in view', () => {
    seedPlan(seeded)
    openWith()
    fireEvent.click(screen.getByRole('tab', { name: 'History' }))

    fireEvent.click(rowHandle('John 3:16'))
    pressKey('Enter')

    expect(lastSent(socket)).toMatchObject({ action: 'display_reference', book: 'John' })
  })

  it('one cursor: highlighting a plan row takes it from the queue', () => {
    seedPlan(seeded)
    render(<App />)
    act(() => {
      socket?.onopen?.()
    })
    const queued = { ...manualReference('Acts', 2, 38), id: 'p-1', source: 'detected' as const }
    push(socket, {
      type: 'state_snapshot',
      data: {
        current_display: null,
        pending: [{ ...queued, confidence: 'high' }],
        history: [],
        pending_count: 1,
        history_count: 0,
        display_connections: 1,
        operator_connections: 1,
        display_settings: { verse_limit: 3, theme: 'dark' },
      },
    })
    fireEvent.click(rowHandle('Acts 2:38'))
    fireEvent.click(rowHandle('John 3:16'))

    expect(rowHandle('Acts 2:38')).toHaveAttribute('aria-pressed', 'false')
    pressKey('Enter')
    expect(sentActions()).toEqual([
      { action: 'display_reference', book: 'John', chapter: 3, verse_start: 16, verse_end: 16 },
    ])
  })

  it('removing the highlighted row leaves nothing for Enter to show', () => {
    seedPlan(seeded)
    openWith()

    fireEvent.click(rowHandle('John 3:16'))
    fireEvent.click(screen.getByRole('button', { name: 'Remove John 3:16 from the plan' }))
    pressKey('Enter')

    expect(sentActions()).toEqual([])
  })

  it('moves to Pending after the only highlighted Plan row is removed', () => {
    seedPlan([seeded[0]])
    openWith()
    const queued = {
      ...manualReference('Acts', 2, 38),
      source: 'detected' as const,
      confidence: 'high' as const,
    }
    push(socket, { type: 'pending_update', data: { pending: [queued], count: 1 } })

    fireEvent.click(rowHandle('John 3:16'))
    expect(rowHandle('John 3:16')).toHaveAttribute('aria-pressed', 'true')
    fireEvent.click(screen.getByRole('button', { name: 'Remove John 3:16 from the plan' }))
    pressKey('ArrowDown')

    expect(rowHandle('Acts 2:38')).toHaveAttribute('aria-pressed', 'true')
    expect(rowHandle('Acts 2:38')).toHaveFocus()
  })
})

describe('plan row actions', () => {
  it('each row has Remove, the reorder arrows and Show, in that order, Show right-most', () => {
    seedPlan(seeded)
    openWith()

    const buttons = within(planRow('Romans 8:28'))
      .getAllByRole('button')
      .filter(button => button.tagName === 'BUTTON')
    expect(buttons.map(button => button.getAttribute('aria-label') ?? button.textContent)).toEqual([
      'Remove Romans 8:28 from the plan',
      'Move Romans 8:28 up',
      'Move Romans 8:28 down',
      'Show',
    ])
    // Remove is an icon: its name is in the label and the tooltip, not on the button.
    const remove = buttons[0]
    expect(remove).toHaveAttribute('title', 'Remove Romans 8:28 from the plan')
    expect(remove).toHaveClass('btn-ghost', 'btn-square')
    expect(remove.textContent).toBe('')
  })

  it('Show sends the passage as coordinates at once, without a lookup', () => {
    seedPlan(seeded)
    openWith()

    fireEvent.click(within(planRow('John 3:16')).getByRole('button', { name: 'Show' }))

    expect(lastSent(socket)).toEqual({
      action: 'display_reference',
      book: 'John',
      chapter: 3,
      verse_start: 16,
      verse_end: 16,
    })
    expect(lookupCalls()).toEqual([])
    expect(planTitles()).toHaveLength(3)

    push(socket, { type: 'display_confirmed', data: { reference: manualReference('John', 3, 16) } })
    expect(screen.getByText('Shown: John 3:16 (manual)')).toBeInTheDocument()
  })

  it('a second Show while the first is in flight is ignored', () => {
    seedPlan(seeded)
    openWith()

    fireEvent.click(within(planRow('John 3:16')).getByRole('button', { name: 'Show' }))
    fireEvent.click(within(planRow('Romans 8:28')).getByRole('button', { name: 'Show' }))

    expect(sentActions()).toHaveLength(1)
    expect(lastSent(socket)).toMatchObject({ action: 'display_reference', book: 'John' })
  })

  it('is disabled while the socket is closed; Remove is not', () => {
    seedPlan(seeded)
    render(<App />)
    const row = within(planPanel().getAllByRole('listitem')[0])

    expect(row.getByRole('button', { name: 'Show' })).toBeDisabled()
    expect(row.getByRole('button', { name: /^Remove / })).toBeEnabled()
  })

  it('a dropped socket sends nothing for Enter on a highlighted row', () => {
    seedPlan(seeded)
    openWith()
    fireEvent.click(rowHandle('John 3:16'))
    act(() => {
      socket?.onclose?.({ code: 1006 })
    })

    pressKey('Enter')

    expect(sentActions()).toEqual([])
  })
})

describe('the current passage tag', () => {
  it('marks the entry whose passage is on the display, by coordinates', () => {
    seedPlan(seeded)
    openWith({ currentDisplay: manualReference('Romans', 8, 28) })

    expect(within(planRow('Romans 8:28')).getByText('On screen')).toHaveClass('tag')
    // The row's name carries the tag too, for a screen reader.
    expect(rowHandle('Romans 8:28')).toHaveAccessibleDescription('On screen')
    expect(rowHandle('John 3:16')).not.toHaveAccessibleDescription()
    expect(within(planRow('John 3:16')).queryByText('On screen')).not.toBeInTheDocument()
    expect(planPanel().getAllByText('On screen')).toHaveLength(1)
  })

  it('shows the badge only while an audience display is connected', () => {
    seedPlan(seeded)
    openWith({ currentDisplay: manualReference('Romans', 8, 28) })
    const currentRow = planRow('Romans 8:28')

    push(socket, { type: 'connection_status', data: { display: 0, operator: 1 } })
    expect(currentRow.querySelector('.tag')).toBeNull()

    push(socket, { type: 'connection_status', data: { display: 1, operator: 1 } })
    expect(within(currentRow).getByText('On screen')).toBeInTheDocument()
  })

  it('hides the tag and accessible description while the display is confirmed off air', () => {
    seedPlan(seeded)
    openWith({ currentDisplay: manualReference('Romans', 8, 28) })
    const currentRow = within(planRow('Romans 8:28'))
    const handle = rowHandle('Romans 8:28')
    expect(currentRow.getByText('On screen')).toBeInTheDocument()
    expect(handle).toHaveAccessibleDescription('On screen')

    push(socket, {
      type: 'display_settings_confirmed',
      data: { verse_limit: 3, theme: 'dark', live: false },
    })
    expect(currentRow.queryByText('On screen')).not.toBeInTheDocument()
    expect(handle).not.toHaveAccessibleDescription()

    push(socket, {
      type: 'display_settings_confirmed',
      data: { verse_limit: 3, theme: 'dark', live: true },
    })
    expect(currentRow.getByText('On screen')).toBeInTheDocument()
    expect(handle).toHaveAccessibleDescription('On screen')
  })

  it('moves with the display and goes when it is cleared', () => {
    seedPlan(seeded)
    openWith({ currentDisplay: manualReference('Romans', 8, 28) })

    push(socket, { type: 'display_confirmed', data: { reference: manualReference('John', 3, 16) } })
    expect(within(planRow('John 3:16')).getByText('On screen')).toBeInTheDocument()
    expect(within(planRow('Romans 8:28')).queryByText('On screen')).not.toBeInTheDocument()

    push(socket, { type: 'clear_confirmed', data: {} })
    expect(planPanel().queryByText('On screen')).not.toBeInTheDocument()
  })

  it('needs the same verses: a passage inside a planned range is not it', () => {
    seedPlan(seeded)
    openWith({ currentDisplay: manualReference('Psalms', 23, 1) })

    expect(planPanel().queryByText('On screen')).not.toBeInTheDocument()
  })
})

describe('the start of the text on plan rows', () => {
  it('shows the remembered text beside the reference', () => {
    seedPlan(seeded)
    openWith()

    expect(planPanel().getByText('John 3:16 text')).toBeInTheDocument()
  })

  it('looks the text up for an item saved without any, and shows it when it lands', async () => {
    seedPlan([{ ...planItem('a', 'John', 3, 16), snippet: '' }])
    openWith()

    await waitFor(() => expect(planPanel().getByText('John 3:16 text')).toBeInTheDocument())
    expect(lookupCalls().map(([url]) => String(url))).toEqual([
      '/api/lookup?book=John&chapter=3&verse_start=16&verse_end=16',
    ])
    expect(sentActions()).toEqual([])
  })

  it('fills out an older saved range while keeping its excerpt visible during the lookup', async () => {
    seedPlan([{ ...planItem('a', 'John', 3, 16, 17), snippetFormat: undefined }])
    deferLookups = true
    openWith()

    expect(planPanel().getByText('John 3:16 text')).toBeInTheDocument()
    await releaseLookups()
    expect(planPanel().getByText('John 3:16 text John 3:17 text')).toBeInTheDocument()
    expect(sentActions()).toEqual([])
  })

  it('shows the reference alone while the lookup is pending, then once it lands', async () => {
    seedPlan([{ ...planItem('a', 'John', 3, 16), snippet: '' }])
    deferLookups = true
    openWith()

    expect(planPanel().getByText('John 3:16')).toBeInTheDocument()
    expect(planPanel().queryByText('John 3:16 text')).not.toBeInTheDocument()

    await releaseLookups()
    expect(planPanel().getByText('John 3:16 text')).toBeInTheDocument()
  })

  it('keeps the row working when the lookup fails', async () => {
    seedPlan([{ ...planItem('a', 'John', 3, 16), snippet: '' }])
    lookupFailure = 'No such passage'
    openWith()

    await waitFor(() => expect(lookupCalls()).toHaveLength(1))
    expect(planPanel().getByText('John 3:16')).toBeInTheDocument()
    expect(planPanel().getByRole('button', { name: 'Show' })).toBeEnabled()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
})

describe('adding a passage to the plan from Find Scripture', () => {
  it('Ctrl+Enter adds the best match, clears the box, and sends nothing', async () => {
    openWith()

    type('john 3:16-17')
    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: 'John 3:16-17 — show on display' })
      ).toBeInTheDocument()
    )
    pressPlanKey('ctrlKey')

    await waitFor(() => expect(searchBox()).toHaveValue(''))
    expect(screen.getByText('Added to the plan: John 3:16-17')).toBeInTheDocument()
    expect(sentActions()).toEqual([])
    expect(planTitles()).toEqual(['John 3:16-17'])
    expect(planPanel().getByText('John 3:16 text John 3:17 text')).toBeInTheDocument()
    expect(storedIds()).toHaveLength(1)
  })

  it.each(['metaKey', 'shiftKey'] as const)(
    '%s+Enter plans too, and never shows the passage',
    async modifier => {
      openWith()

      type('john 3:16-17')
      await waitFor(() =>
        expect(
          screen.getByRole('button', { name: 'John 3:16-17 — show on display' })
        ).toBeInTheDocument()
      )
      pressPlanKey(modifier)

      await waitFor(() => expect(planTitles()).toEqual(['John 3:16-17']))
      expect(sentActions()).toEqual([])
    }
  )

  it('the Plan button adds the passage the same way', async () => {
    openWith()

    type('john 3:16-17')
    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: 'John 3:16-17 — add to the plan' })
      ).toBeInTheDocument()
    )
    fireEvent.click(screen.getByRole('button', { name: 'John 3:16-17 — add to the plan' }))

    await waitFor(() => expect(searchBox()).toHaveValue(''))
    expect(screen.getByText('Added to the plan: John 3:16-17')).toBeInTheDocument()
    expect(sentActions()).toEqual([])
    expect(planTitles()).toEqual(['John 3:16-17'])
  })

  it('adding a passage already in the plan says so and keeps one copy', async () => {
    openWith()

    type('john 3:16-17')
    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: 'John 3:16-17 — add to the plan' })
      ).toBeInTheDocument()
    )
    fireEvent.click(screen.getByRole('button', { name: 'John 3:16-17 — add to the plan' }))
    await waitFor(() => expect(searchBox()).toHaveValue(''))

    type('john 3:16-17')
    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: 'John 3:16-17 — show on display' })
      ).toBeInTheDocument()
    )
    pressPlanKey()

    await waitFor(() =>
      expect(screen.getByText('John 3:16-17 is already in the plan')).toBeInTheDocument()
    )
    expect(planTitles()).toEqual(['John 3:16-17'])
    expect(sentActions()).toEqual([])
  })
})
