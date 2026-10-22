import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import App from './App'
import { PLAN_STORAGE_KEY } from './hooks/usePlan'
import type { PlanItem } from './hooks/usePlan'
import type { BibleReference, OperatorMessage } from '../shared/types'

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

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as unknown as Response
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
  return view
}

/** Open a tab of the sidebar, the way the operator does. */
function openTab(name: string | RegExp) {
  fireEvent.click(screen.getByRole('tab', { name }))
}

/** The Plan tab, opened: its list is the only body in the tab panel. */
function planPanel() {
  openTab(/^Plan/)
  return within(screen.getByRole('tabpanel'))
}

/** The plan rows' titles, top to bottom. */
function planTitles() {
  return within(screen.getByRole('tabpanel'))
    .queryAllByRole('listitem')
    .map(row => row.querySelector('.plan-row-title')?.textContent)
}

/** The number each plan row shows, top to bottom. */
function planNumbers() {
  return within(screen.getByRole('tabpanel'))
    .queryAllByRole('listitem')
    .map(row => row.querySelector('.plan-row-number')?.textContent)
}

/** A plan row's main button: a click stages it, a double-click shows it. */
function stageButton(label: string) {
  return screen.getByRole('button', { name: `Stage ${label} from the plan in Up Next` })
}

function searchBox() {
  return screen.getByLabelText('Find scripture by book or reference')
}

/** Type into the search box (the results arrive after the debounce). */
function type(text: string) {
  fireEvent.change(searchBox(), { target: { value: text } })
}

/** Shift+Enter in the search box: add the best match to the plan. */
function pressShiftEnter() {
  act(() => {
    fireEvent.keyDown(searchBox(), { key: 'Enter', shiftKey: true })
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

describe('the plan panel', () => {
  it('lists the planned passages in order, numbered', () => {
    seedPlan(seeded)
    openWith()
    planPanel()

    expect(planTitles()).toEqual(['John 3:16', 'Romans 8:28', 'Psalms 23:1-6'])
    expect(planNumbers()).toEqual(['1', '2', '3'])
  })

  it('says so when nothing is planned, and offers no Clear', () => {
    openWith()
    planPanel()

    expect(screen.getByText('Nothing planned')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Clear plan' })).toBeDisabled()
  })

  it('moves an item up or down, and the numbers and the stored order follow', () => {
    seedPlan(seeded)
    openWith()
    planPanel()

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
    planPanel()

    fireEvent.click(screen.getByRole('button', { name: 'Remove Romans 8:28 from the plan' }))

    expect(planTitles()).toEqual(['John 3:16', 'Psalms 23:1-6'])
    expect(planNumbers()).toEqual(['1', '2'])
    expect(storedIds()).toEqual(['a', 'c'])
  })

  it('clears the plan only once the operator confirms', () => {
    seedPlan(seeded)
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
    openWith()
    planPanel()

    fireEvent.click(screen.getByRole('button', { name: 'Clear plan' }))
    expect(confirm).toHaveBeenCalledWith('Remove every passage from the plan?')
    expect(planTitles()).toHaveLength(3)

    confirm.mockReturnValue(true)
    fireEvent.click(screen.getByRole('button', { name: 'Clear plan' }))

    expect(screen.getByText('Nothing planned')).toBeInTheDocument()
    expect(storedIds()).toEqual([])
  })

  it('keeps the plan and its order across a reload', () => {
    seedPlan(seeded)
    const first = openWith()
    planPanel()
    fireEvent.click(screen.getByRole('button', { name: 'Move Romans 8:28 up' }))
    first.unmount()

    openWith()
    planPanel()

    expect(planTitles()).toEqual(['Romans 8:28', 'John 3:16', 'Psalms 23:1-6'])
  })

  it('a click stages the passage in Up Next after a fresh lookup, and sends nothing', async () => {
    seedPlan(seeded)
    openWith()
    planPanel()

    fireEvent.click(stageButton('Romans 8:28'))

    await waitFor(() => expect(screen.getByTestId('next-preview')).toHaveTextContent('Romans 8:28'))
    expect(
      within(screen.getByTestId('next-verses')).getByText('Romans 8:28 text')
    ).toBeInTheDocument()
    expect(lookupCalls().map(([url]) => String(url))).toEqual([
      '/api/lookup?book=Romans&chapter=8&verse_start=28&verse_end=28',
    ])
    expect(sentActions()).toEqual([])
    // Staging does not take the passage out of the plan.
    expect(planTitles()).toHaveLength(3)
  })

  it('a double-click shows the passage at once, with no staging and no lookup', () => {
    seedPlan(seeded)
    openWith()
    planPanel()

    fireEvent.doubleClick(stageButton('John 3:16'))

    expect(sentActions()).toHaveLength(1)
    expect(lastSent()).toEqual({
      action: 'display_reference',
      book: 'John',
      chapter: 3,
      verse_start: 16,
      verse_end: 16,
    })
    expect(screen.getByText('Nothing staged')).toBeInTheDocument()
    expect(lookupCalls()).toEqual([])
    // A verse is often read twice, so a shown item stays in the plan.
    expect(planTitles()).toHaveLength(3)

    push({ type: 'display_confirmed', data: { reference: manualReference('John', 3, 16) } })
    expect(screen.getByText('Shown: John 3:16 (manual)')).toBeInTheDocument()
  })

  it('a lookup started by the clicks before a double-click never stages the passage', async () => {
    seedPlan(seeded)
    deferLookups = true
    openWith()
    planPanel()

    fireEvent.click(stageButton('John 3:16'))
    fireEvent.click(stageButton('John 3:16'))
    fireEvent.doubleClick(stageButton('John 3:16'))
    expect(sentActions()).toHaveLength(1)

    // The lookups the clicks started answer only now, after the display has the passage.
    await releaseLookups()

    expect(screen.getByText('Nothing staged')).toBeInTheDocument()
    expect(screen.queryByTestId('next-preview')).not.toBeInTheDocument()
    expect(sentActions()).toHaveLength(1)
  })
})

describe('plan row actions', () => {
  /** The plan row for a passage, found by its title. */
  function planRow(label: string) {
    const row = within(screen.getByRole('tabpanel'))
      .getAllByRole('listitem')
      .find(item => item.querySelector('.plan-row-title')?.textContent === label)
    if (!row) throw new Error(`no plan row for ${label}`)
    return within(row)
  }

  it('each row has Show, Stage and Remove, like a pending row has Show and Stage', () => {
    seedPlan(seeded)
    openWith()
    planPanel()

    const row = planRow('Romans 8:28')
    expect(row.getByRole('button', { name: 'Show' })).toBeInTheDocument()
    expect(row.getByRole('button', { name: 'Stage' })).toBeInTheDocument()
    expect(row.getByRole('button', { name: 'Remove Romans 8:28 from the plan' })).toHaveTextContent(
      'Remove'
    )
  })

  it('Show sends the passage as coordinates at once, without staging or a lookup', () => {
    seedPlan(seeded)
    openWith()
    planPanel()

    fireEvent.click(planRow('John 3:16').getByRole('button', { name: 'Show' }))

    expect(lastSent()).toEqual({
      action: 'display_reference',
      book: 'John',
      chapter: 3,
      verse_start: 16,
      verse_end: 16,
    })
    expect(lookupCalls()).toEqual([])
    expect(screen.getByText('Nothing staged')).toBeInTheDocument()
    // A shown item stays in the plan: a verse is often read twice.
    expect(planTitles()).toHaveLength(3)
  })

  it('Stage looks the passage up and stages it in Up Next, sending nothing', async () => {
    seedPlan(seeded)
    openWith()
    planPanel()

    fireEvent.click(planRow('Romans 8:28').getByRole('button', { name: 'Stage' }))

    await waitFor(() => expect(screen.getByTestId('next-preview')).toHaveTextContent('Romans 8:28'))
    expect(sentActions()).toEqual([])
  })

  it('Remove takes the row out of the plan', () => {
    seedPlan(seeded)
    openWith()
    planPanel()

    fireEvent.click(planRow('Romans 8:28').getByRole('button', { name: /^Remove Romans 8:28/ }))

    expect(planTitles()).toEqual(['John 3:16', 'Psalms 23:1-6'])
    expect(sentActions()).toEqual([])
  })
})

describe('plan row Show while disconnected', () => {
  it('is disabled while the socket is closed, as a pending row is; Stage and Remove are not', () => {
    seedPlan(seeded)
    render(<App />)
    const panel = planPanel()
    const row = within(panel.getAllByRole('listitem')[0])

    expect(row.getByRole('button', { name: 'Show' })).toBeDisabled()
    expect(row.getByRole('button', { name: 'Stage' })).toBeEnabled()
    expect(row.getByRole('button', { name: /^Remove / })).toBeEnabled()
  })
})

describe('the start of the text on plan rows', () => {
  it('shows the remembered text under the reference', () => {
    seedPlan(seeded)
    openWith()
    const panel = planPanel()

    expect(panel.getByText('John 3:16 text')).toBeInTheDocument()
  })

  it('looks the text up for an item saved without any, and shows it when it lands', async () => {
    seedPlan([{ ...planItem('a', 'John', 3, 16), snippet: '' }])
    openWith()
    const panel = planPanel()

    await waitFor(() => expect(panel.getByText('John 3:16 text')).toBeInTheDocument())
    expect(lookupCalls().map(([url]) => String(url))).toEqual([
      '/api/lookup?book=John&chapter=3&verse_start=16&verse_end=16',
    ])
    expect(sentActions()).toEqual([])
  })

  it('shows the reference alone while the lookup is pending, then once it lands', async () => {
    seedPlan([{ ...planItem('a', 'John', 3, 16), snippet: '' }])
    deferLookups = true
    openWith()
    const panel = planPanel()

    expect(panel.getByText('John 3:16')).toBeInTheDocument()
    expect(panel.queryByText('John 3:16 text')).not.toBeInTheDocument()

    await releaseLookups()
    expect(panel.getByText('John 3:16 text')).toBeInTheDocument()
  })

  it('keeps the row working when the lookup fails', async () => {
    seedPlan([{ ...planItem('a', 'John', 3, 16), snippet: '' }])
    lookupFailure = 'No such passage'
    openWith()
    const panel = planPanel()

    await waitFor(() => expect(lookupCalls()).toHaveLength(1))
    expect(panel.getByText('John 3:16')).toBeInTheDocument()
    expect(panel.getByRole('button', { name: 'Show' })).toBeEnabled()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
})

describe('adding a passage to the plan from Find Scripture', () => {
  it('Shift+Enter adds the best match, clears the box, and sends nothing', async () => {
    openWith()

    type('john 3:16-17')
    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: 'John 3:16-17 — stage in Up Next' })
      ).toBeInTheDocument()
    )
    pressShiftEnter()

    await waitFor(() => expect(searchBox()).toHaveValue(''))
    expect(screen.getByText('Added to the plan: John 3:16-17')).toBeInTheDocument()
    expect(sentActions()).toEqual([])
    expect(screen.queryByTestId('next-preview')).not.toBeInTheDocument()

    planPanel()
    expect(planTitles()).toEqual(['John 3:16-17'])
    expect(storedIds()).toHaveLength(1)
  })

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
    planPanel()
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
        screen.getByRole('button', { name: 'John 3:16-17 — stage in Up Next' })
      ).toBeInTheDocument()
    )
    pressShiftEnter()

    await waitFor(() =>
      expect(screen.getByText('John 3:16-17 is already in the plan')).toBeInTheDocument()
    )
    planPanel()
    expect(planTitles()).toEqual(['John 3:16-17'])
    expect(sentActions()).toEqual([])
  })
})

describe('plan rows when a lookup fails or the socket is closed', () => {
  it('a click whose lookup is refused says why in a notice, and stages nothing', async () => {
    seedPlan(seeded)
    lookupFailure = 'Romans 8:28 is not in the Bible text available on this server.'
    openWith()
    planPanel()

    fireEvent.click(stageButton('Romans 8:28'))

    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(lookupFailure ?? ''))
    expect(screen.queryByTestId('next-preview')).not.toBeInTheDocument()
    expect(sentActions()).toEqual([])
  })

  it('a double-click while the socket is closed sends nothing', () => {
    seedPlan(seeded)
    openWith()
    planPanel()
    act(() => {
      socket?.onclose?.({ code: 1006 })
    })

    fireEvent.doubleClick(stageButton('John 3:16'))

    expect(sentActions()).toEqual([])
    expect(planTitles()).toHaveLength(3)
  })
})

describe('double-clicks on the plan share one in-flight guard', () => {
  it('a second double-click while the first is in flight is ignored', () => {
    seedPlan(seeded)
    openWith()
    planPanel()

    fireEvent.doubleClick(stageButton('John 3:16'))
    fireEvent.doubleClick(stageButton('Romans 8:28'))

    expect(sentActions()).toHaveLength(1)
    expect(lastSent()).toMatchObject({ action: 'display_reference', book: 'John' })
  })
})
