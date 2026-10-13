import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import App from './App'
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

function reference(id: string, book: string, chapter: number, verses: number[]): BibleReference {
  return {
    id,
    book,
    chapter,
    verse_start: verses[0],
    verse_end: verses[verses.length - 1],
    confidence: 'high',
    inferred: false,
    text: `${book} ${chapter} detected`,
    verses: verses.map(verse => ({ verse, text: `${book} ${chapter}:${verse} text` })),
    created_at: '2026-01-01T00:00:00Z',
  }
}

const romans = reference('p-1', 'Romans', 8, [28])

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
 * A slice of the canon with the abbreviations and name variants the server
 * serves. Deliberately wide: more than a dozen books match a single letter, so
 * the results cap and its "Show more" button are exercised for real.
 */
const BOOKS = {
  books: [
    { name: 'Genesis', chapters: 50, order: 1, abbrev: 'gn', variants: ['genesis'] },
    { name: 'Exodus', chapters: 40, order: 2, abbrev: 'ex', variants: ['exodus'] },
    { name: 'Leviticus', chapters: 27, order: 3, abbrev: 'lv', variants: ['leviticus'] },
    { name: 'Numbers', chapters: 36, order: 4, abbrev: 'nm', variants: ['numbers'] },
    { name: 'Deuteronomy', chapters: 34, order: 5, abbrev: 'dt', variants: ['deuteronomy'] },
    { name: 'Joshua', chapters: 24, order: 6, abbrev: 'js', variants: ['joshua'] },
    { name: 'Judges', chapters: 21, order: 7, abbrev: 'jd', variants: ['judges'] },
    { name: 'Psalms', chapters: 150, order: 19, abbrev: 'ps', variants: ['psalms', 'psalm'] },
    { name: 'Isaiah', chapters: 66, order: 23, abbrev: 'is', variants: ['isaiah'] },
    { name: 'Jeremiah', chapters: 52, order: 24, abbrev: 'jr', variants: ['jeremiah'] },
    { name: 'Lamentations', chapters: 5, order: 25, abbrev: 'lm', variants: ['lamentations'] },
    { name: 'Daniel', chapters: 12, order: 27, abbrev: 'dn', variants: ['daniel'] },
    { name: 'Hosea', chapters: 14, order: 28, abbrev: 'ho', variants: ['hosea'] },
    { name: 'Amos', chapters: 9, order: 30, abbrev: 'am', variants: ['amos'] },
    { name: 'Obadiah', chapters: 1, order: 31, abbrev: 'ob', variants: ['obadiah'] },
    { name: 'Micah', chapters: 7, order: 33, abbrev: 'mi', variants: ['micah'] },
    { name: 'Nahum', chapters: 3, order: 34, abbrev: 'na', variants: ['nahum'] },
    { name: 'Habakkuk', chapters: 3, order: 35, abbrev: 'hk', variants: ['habakkuk'] },
    { name: 'Matthew', chapters: 28, order: 40, abbrev: 'mt', variants: ['matthew'] },
    { name: 'Mark', chapters: 16, order: 41, abbrev: 'mk', variants: ['mark'] },
    { name: 'Luke', chapters: 24, order: 42, abbrev: 'lk', variants: ['luke'] },
    { name: 'John', chapters: 21, order: 43, abbrev: 'jn', variants: ['john'] },
    { name: 'Acts', chapters: 28, order: 44, abbrev: 'act', variants: ['acts'] },
    { name: 'Romans', chapters: 16, order: 45, abbrev: 'rm', variants: ['romans'] },
    { name: '1 John', chapters: 5, order: 62, abbrev: '1jo', variants: ['john'] },
    {
      name: 'Song of Solomon',
      chapters: 8,
      order: 22,
      abbrev: 'so',
      variants: ['song of solomon', 'song of songs', 'canticles'],
    },
  ],
}

/** The parts of a Response the API client reads. */
function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as unknown as Response
}

/**
 * The mocked fetch: routes the two endpoints the search panel calls, and answers
 * the console's own spoken-language read the way the server would.
 */
function createFetchMock() {
  return vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
    const url = String(input)
    if (url.startsWith('/api/language')) {
      return jsonResponse({ language: 'en' })
    }
    if (url.startsWith('/api/books')) {
      return jsonResponse(BOOKS)
    }
    if (lookupStatus !== 200) {
      return jsonResponse({ detail: lookupErrorDetail }, lookupStatus)
    }
    return jsonResponse(passageBody(url))
  })
}

type FetchMock = ReturnType<typeof createFetchMock>

let fetchMock: FetchMock
let lookupStatus = 200
let lookupErrorDetail = ''

/**
 * Requests the search made. The console reads the spoken language itself when it
 * connects, which is not the search's doing, so those calls are set aside.
 */
function searchCalls() {
  return fetchMock.mock.calls.filter(([url]) => !String(url).startsWith('/api/language'))
}

function stubFetch() {
  lookupStatus = 200
  lookupErrorDetail = ''
  fetchMock = createFetchMock()
  vi.stubGlobal('fetch', fetchMock)
}

/** Connect the socket and deliver a snapshot with the given display state. */
function openWith(
  options: { currentDisplay?: BibleReference | null; pending?: BibleReference[] } = {}
) {
  const { currentDisplay = null, pending = [] } = options
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
      display_settings: { verse_limit: 3, theme: 'dark' },
    },
  })
}

/** The search box. */
function searchBox() {
  return screen.getByLabelText('Find scripture by book or reference')
}

/** Type into the search box (the results arrive after the debounce). */
function type(text: string) {
  fireEvent.change(searchBox(), { target: { value: text } })
}

/** The results list, scoped so book and passage rows are counted together. */
function results() {
  return within(screen.getByRole('list', { name: 'Scripture search results' }))
}

/** The pending queue, scoped so the search rows cannot be counted with it. */
function queueSection() {
  const section = screen.getByRole('heading', { name: 'Pending References' }).closest('section')
  if (!section) throw new Error('pending section not found')
  return within(section)
}

/** Press Enter in the search box: the console's fast path through the flow. */
function pressEnter() {
  act(() => {
    fireEvent.keyDown(searchBox(), { key: 'Enter' })
  })
}

/** Press Shift+Enter in the search box: the direct-to-display shortcut. */
function pressShiftEnter() {
  act(() => {
    fireEvent.keyDown(searchBox(), { key: 'Enter', shiftKey: true })
  })
}

/** The header gear that opens Settings. */
function settingsGear() {
  return screen.getByRole('button', { name: 'Settings' })
}

/** A click on the row whose accessible name matches. */
function clickResult(name: string | RegExp) {
  act(() => {
    results().getByRole('button', { name }).click()
  })
}

function showNext() {
  act(() => {
    screen.getByRole('button', { name: 'Show' }).click()
  })
}

/** The server-built reference that answers a manual display request. */
function manualReference(
  book: string,
  chapter: number,
  verseStart: number,
  verseEnd: number
): BibleReference {
  return {
    id: 'manual-1',
    book,
    chapter,
    verse_start: verseStart,
    verse_end: verseEnd,
    confidence: null,
    inferred: false,
    source: 'manual',
    text: `${book} ${chapter}:${verseStart} text`,
    verses: [{ verse: verseStart, text: `${book} ${chapter}:${verseStart} text` }],
    created_at: '2026-01-01T00:00:00Z',
  }
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
  stubFetch()
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  socket = null
})

describe('Find Scripture', () => {
  it('is a single text box, and asks for nothing until it is used', () => {
    openWith()

    expect(searchBox()).toBeEnabled()
    // No book picker, chapter box, or Find button left over from the form.
    expect(screen.queryByLabelText('Chapter')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Find' })).not.toBeInTheDocument()
    // An unused console does not fetch the canon.
    expect(searchCalls()).toEqual([])
  })

  it('waits for typing to settle before matching', async () => {
    openWith()

    type('ge')

    // The debounce means nothing has been matched from the keystroke itself.
    expect(screen.queryByRole('button', { name: /^Genesis/ })).not.toBeInTheDocument()

    await waitFor(() =>
      expect(screen.getByRole('button', { name: /^Genesis/ })).toBeInTheDocument()
    )
    // The book list came from the server, with the operator's token.
    expect(searchCalls()[0][0]).toBe('/api/books')
  })

  it('matches a partial book name by substring and marks the matched text', async () => {
    openWith()

    type('enes') // middle of "Genesis", not a prefix

    await waitFor(() =>
      expect(screen.getByRole('button', { name: /^Genesis/ })).toBeInTheDocument()
    )
    const marked = results().getByText('enes')
    expect(marked.tagName).toBe('MARK')
    // Nothing else in the canon matches that substring.
    expect(results().getAllByRole('button')).toHaveLength(1)
  })

  it('matches an abbreviation and a name variant from the canon', async () => {
    openWith()

    type('jn')
    await waitFor(() => expect(screen.getByRole('button', { name: /^John/ })).toBeInTheDocument())

    type('canticles')
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /^Song of Solomon/ })).toBeInTheDocument()
    )
  })

  it('caps the list and reveals the rest with Show more', async () => {
    openWith()

    // Eighteen books contain an "a"; the list shows a dozen and offers the rest.
    type('a')
    await waitFor(() => expect(results().getAllByRole('button').length).toBeGreaterThan(10))
    expect(results().getAllByRole('button')).toHaveLength(12)

    const more = screen.getByRole('button', { name: /Show \d+ more/ })
    act(() => {
      more.click()
    })

    expect(results().getAllByRole('button').length).toBeGreaterThan(12)
  })

  it('resolves "ge 1 1" to Genesis 1:1', async () => {
    openWith()

    type('ge 1 1')

    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: 'Genesis 1:1 — stage in Up Next' })
      ).toBeInTheDocument()
    )
    // A complete reference is the whole answer: no book list under it.
    expect(results().getAllByRole('button')).toHaveLength(1)
  })

  it('resolves "gen 1 1-3" to Genesis 1:1-3', async () => {
    openWith()

    type('gen 1 1-3')

    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: 'Genesis 1:1-3 — stage in Up Next' })
      ).toBeInTheDocument()
    )
  })

  it('resolves "john 3:16" and "ps 23" in the server\'s own conventions', async () => {
    openWith()

    type('john 3:16')
    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: 'John 3:16 — stage in Up Next' })
      ).toBeInTheDocument()
    )

    // A chapter with no verse reads as the chapter, which the server opens at
    // verse 1 — the same convention the manual form used.
    type('ps 23')
    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: 'Psalms 23 — stage in Up Next' })
      ).toBeInTheDocument()
    )
    expect(results().getByText(/Whole chapter/)).toBeInTheDocument()
  })

  it('keeps "john 3:16" on John rather than 1 John', async () => {
    openWith()

    type('john 3:16')

    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: 'John 3:16 — stage in Up Next' })
      ).toBeInTheDocument()
    )
    expect(screen.queryByRole('button', { name: /1 John/ })).not.toBeInTheDocument()
  })

  it('reports a name it cannot match instead of guessing', async () => {
    openWith()

    type('genesisish')

    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent('No book matches "genesisish".')
    )
    expect(screen.queryByRole('list', { name: 'Scripture search results' })).not.toBeInTheDocument()
  })

  it('reports a chapter beyond the book before asking the server', async () => {
    openWith()

    type('gen 51')

    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent('Genesis has 50 chapters.')
    )
    // The book list only; no lookup was attempted.
    expect(fetchMock.mock.calls.filter(([url]) => String(url).startsWith('/api/lookup'))).toEqual(
      []
    )
    expect(screen.getByText('Nothing staged')).toBeInTheDocument()
  })

  it('reports a reversed verse range', async () => {
    openWith()

    type('john 3 16-15')

    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent(
        'The verse range must start at or before its end.'
      )
    )
    expect(screen.queryByTestId('next-preview')).not.toBeInTheDocument()
  })

  it('shows the server’s rejection of a verse that is not in its text', async () => {
    openWith()

    lookupStatus = 404
    lookupErrorDetail = 'John 3:999 is not in the Bible text available on this server.'
    type('john 3:999')
    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: 'John 3:999 — stage in Up Next' })
      ).toBeInTheDocument()
    )

    clickResult('John 3:999 — stage in Up Next')

    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent(
        'John 3:999 is not in the Bible text available on this server.'
      )
    )
    expect(screen.queryByTestId('next-preview')).not.toBeInTheDocument()
    expect(socket?.send).not.toHaveBeenCalled()
  })

  it('stages a clicked passage in Up Next with its verse text', async () => {
    openWith({ currentDisplay: romans })

    type('john 3:16-17')
    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: 'John 3:16-17 — stage in Up Next' })
      ).toBeInTheDocument()
    )
    clickResult('John 3:16-17 — stage in Up Next')
    await waitFor(() => expect(screen.getByTestId('next-preview')).toBeInTheDocument())

    const staged = within(screen.getByTestId('next-preview'))
    expect(staged.getByText('John 3:16-17')).toBeInTheDocument()
    expect(
      within(screen.getByTestId('next-verses')).getByText('John 3:16 text')
    ).toBeInTheDocument()
    expect(screen.getByText('Staged: John 3:16-17 — press Show to display')).toBeInTheDocument()

    // The lookup carried the parsed coordinates and nothing else.
    const lookupCall = fetchMock.mock.calls.find(([url]) => String(url).startsWith('/api/lookup'))
    expect(String(lookupCall?.[0])).toBe(
      '/api/lookup?book=John&chapter=3&verse_start=16&verse_end=17'
    )

    // Staging is local: the display is untouched and nothing was sent.
    expect(
      within(screen.getByTestId('live-verses')).getByText('Romans 8:28 text')
    ).toBeInTheDocument()
    expect(socket?.send).not.toHaveBeenCalled()
  })

  it('stages the best result on Enter', async () => {
    openWith({ currentDisplay: romans })

    type('ge 1 1')
    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: 'Genesis 1:1 — stage in Up Next' })
      ).toBeInTheDocument()
    )

    pressEnter()

    await waitFor(() => expect(screen.getByTestId('next-preview')).toHaveTextContent('Genesis 1:1'))
    expect(
      within(screen.getByTestId('live-verses')).getByText('Romans 8:28 text')
    ).toBeInTheDocument()
    expect(socket?.send).not.toHaveBeenCalled()
  })

  it('completes the text when Enter picks a book, and stages nothing', async () => {
    openWith()

    type('gen')
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /^Genesis/ })).toBeInTheDocument()
    )

    pressEnter()

    // The canonical name is in the box, ready for a chapter; no lookup ran.
    await waitFor(() => expect(searchBox()).toHaveValue('Genesis '))
    expect(fetchMock.mock.calls.filter(([url]) => String(url).startsWith('/api/lookup'))).toEqual(
      []
    )
    expect(screen.getByText('Nothing staged')).toBeInTheDocument()
  })

  it('never puts a manual passage in the pending queue', async () => {
    openWith({ pending: [romans] })

    type('john 3:16-17')
    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: 'John 3:16-17 — stage in Up Next' })
      ).toBeInTheDocument()
    )
    clickResult('John 3:16-17 — stage in Up Next')
    await waitFor(() => expect(screen.getByTestId('next-preview')).toBeInTheDocument())

    // It appears in Up Next and nowhere else: the queue still holds the one
    // detection that was already there, and the lookups sent nothing.
    const queue = queueSection()
    expect(queue.getAllByRole('heading', { level: 3 })).toHaveLength(1)
    expect(queue.getByText('Romans 8:28')).toBeInTheDocument()
    expect(within(screen.getByTestId('next-preview')).getByText('John 3:16-17')).toBeInTheDocument()
    expect(screen.getByText('Manual')).toBeInTheDocument()
    expect(socket?.send).not.toHaveBeenCalled()
  })

  it('reports verses the display limit hides', async () => {
    openWith()

    type('gen 1 1-5')
    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: 'Genesis 1:1-5 — stage in Up Next' })
      ).toBeInTheDocument()
    )
    clickResult('Genesis 1:1-5 — stage in Up Next')
    await waitFor(() => expect(screen.getByTestId('next-preview')).toBeInTheDocument())

    expect(screen.getByText('+2 more verses hidden by the current limit')).toBeInTheDocument()
  })

  it('sends the passage coordinates on Show and waits for the server to confirm', async () => {
    openWith({ currentDisplay: romans })

    type('john 3:16-17')
    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: 'John 3:16-17 — stage in Up Next' })
      ).toBeInTheDocument()
    )
    clickResult('John 3:16-17 — stage in Up Next')
    await waitFor(() => expect(screen.getByTestId('next-preview')).toBeInTheDocument())

    showNext()

    expect(lastSent()).toEqual({
      action: 'display_reference',
      book: 'John',
      chapter: 3,
      verse_start: 16,
      verse_end: 17,
    })
    // Sent is not shown: LIVE still holds what the projector had.
    expect(
      within(screen.getByTestId('live-verses')).getByText('Romans 8:28 text')
    ).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Show' })).toBeDisabled()

    push({ type: 'display_confirmed', data: { reference: manualReference('John', 3, 16, 17) } })

    expect(
      within(screen.getByTestId('live-verses')).getByText('John 3:16 text')
    ).toBeInTheDocument()
    expect(screen.getByText('Nothing staged')).toBeInTheDocument()
    expect(screen.getByText('Shown: John 3:16-17 (manual)')).toBeInTheDocument()
  })

  it('keeps the passage staged and the display unchanged when Show is rejected', async () => {
    openWith({ currentDisplay: romans })

    type('john 3:16-17')
    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: 'John 3:16-17 — stage in Up Next' })
      ).toBeInTheDocument()
    )
    clickResult('John 3:16-17 — stage in Up Next')
    await waitFor(() => expect(screen.getByTestId('next-preview')).toBeInTheDocument())

    showNext()
    push({
      type: 'error',
      data: { action: 'display_reference', message: 'Genesis 1:60 is not in the Bible text' },
    })

    expect(screen.getByRole('alert')).toHaveTextContent(
      'Could not show that passage: Genesis 1:60 is not in the Bible text'
    )
    expect(
      within(screen.getByTestId('live-verses')).getByText('Romans 8:28 text')
    ).toBeInTheDocument()
    // Still staged, so the operator can fix the passage or retry.
    expect(within(screen.getByTestId('next-preview')).getByText('John 3:16-17')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Show' })).toBeEnabled()
  })

  it('returns to a queue selection when a pending reference is chosen instead', async () => {
    openWith({ pending: [romans] })

    type('john 3:16-17')
    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: 'John 3:16-17 — stage in Up Next' })
      ).toBeInTheDocument()
    )
    clickResult('John 3:16-17 — stage in Up Next')
    await waitFor(() => expect(screen.getByText('Manual')).toBeInTheDocument())

    act(() => {
      screen.getByRole('button', { name: /Romans 8:28/ }).click()
    })

    expect(screen.queryByText('Manual')).not.toBeInTheDocument()
    expect(screen.getByText('Detected')).toBeInTheDocument()
    expect(within(screen.getByTestId('next-preview')).getByText('Romans 8:28')).toBeInTheDocument()

    // Show now means the ordinary approval, by ID.
    showNext()
    expect(lastSent()).toEqual({ action: 'approve', reference_id: 'p-1' })
  })

  it('offers a retry when the book list cannot be loaded', async () => {
    openWith()
    fetchMock.mockImplementationOnce(async () => {
      throw new TypeError('Failed to fetch')
    })

    type('ge')

    await waitFor(() => expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument())
    expect(screen.getByRole('alert')).toHaveTextContent(
      'Could not reach the server to load the book list.'
    )

    act(() => {
      screen.getByRole('button', { name: 'Retry' }).click()
    })
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /^Genesis/ })).toBeInTheDocument()
    )
  })

  it('disables the box while the socket is closed and re-enables it on reconnect', async () => {
    openWith()

    type('john 3:16')
    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: 'John 3:16 — stage in Up Next' })
      ).toBeInTheDocument()
    )

    act(() => {
      socket?.onclose?.({ code: 1006 })
    })

    expect(screen.getByText(/Not connected to the server/)).toBeInTheDocument()
    expect(searchBox()).toBeDisabled()

    act(() => {
      screen.getByRole('button', { name: 'Reconnect' }).click()
    })
    act(() => {
      socket?.onopen?.()
    })
    push({
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
      },
    })

    expect(searchBox()).toBeEnabled()
  })
})

describe('Shift+Enter direct display', () => {
  it('sends the resolved passage straight to the display without staging it', async () => {
    openWith({ currentDisplay: romans })

    type('john 3:16-17')
    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: 'John 3:16-17 — stage in Up Next' })
      ).toBeInTheDocument()
    )

    pressShiftEnter()

    // One request with the parsed coordinates — the same action Show sends —
    // and no lookup, no staging, nothing else.
    expect(socket?.send).toHaveBeenCalledTimes(1)
    expect(lastSent()).toEqual({
      action: 'display_reference',
      book: 'John',
      chapter: 3,
      verse_start: 16,
      verse_end: 17,
    })
    expect(fetchMock.mock.calls.filter(([url]) => String(url).startsWith('/api/lookup'))).toEqual(
      []
    )
    expect(screen.getByText('Nothing staged')).toBeInTheDocument()
    expect(
      within(screen.getByTestId('live-verses')).getByText('Romans 8:28 text')
    ).toBeInTheDocument()
  })

  it('updates the display when the server confirms the direct request', async () => {
    openWith({ currentDisplay: romans })

    type('john 3:16-17')
    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: 'John 3:16-17 — stage in Up Next' })
      ).toBeInTheDocument()
    )

    pressShiftEnter()
    push({ type: 'display_confirmed', data: { reference: manualReference('John', 3, 16, 17) } })

    expect(
      within(screen.getByTestId('live-verses')).getByText('John 3:16 text')
    ).toBeInTheDocument()
    expect(screen.getByText('Shown: John 3:16-17 (manual)')).toBeInTheDocument()
    // Still nothing staged: the direct path never touched Up Next.
    expect(screen.getByText('Nothing staged')).toBeInTheDocument()
  })

  it('sends nothing for a book row or an invalid reference', async () => {
    openWith()

    // A book is not a passage: there are no coordinates to send.
    type('gen')
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /^Genesis/ })).toBeInTheDocument()
    )
    pressShiftEnter()
    expect(socket?.send).not.toHaveBeenCalled()

    // An errored query is answered, not displayed.
    type('gen 51')
    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent('Genesis has 50 chapters.')
    )
    pressShiftEnter()
    expect(socket?.send).not.toHaveBeenCalled()
  })

  it('sends nothing while Settings is open', async () => {
    openWith()

    type('john 3:16')
    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: 'John 3:16 — stage in Up Next' })
      ).toBeInTheDocument()
    )

    act(() => {
      settingsGear().click()
    })
    expect(screen.getByRole('dialog')).toBeInTheDocument()

    pressShiftEnter()
    expect(socket?.send).not.toHaveBeenCalled()
  })
})
