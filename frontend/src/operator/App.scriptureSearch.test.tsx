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
  return { book, chapter, verse_start: start, verse_end: end, verses, text: verses.map(v => v.text).join(' ') }
}

const BOOKS = {
  books: [
    { name: 'Genesis', chapters: 50, order: 1 },
    { name: 'John', chapters: 21, order: 43 },
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

/** The mocked fetch: routes the two endpoints the search panel calls. */
function createFetchMock() {
  return vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
    const url = String(input)
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

/** Open Find Scripture and wait for the book list the server returns. */
async function openSearch() {
  act(() => {
    screen.getByRole('button', { name: 'Open' }).click()
  })
  await waitFor(() => expect(screen.getByRole('option', { name: 'John' })).toBeInTheDocument())
}

function setField(label: string, value: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } })
}

/** Fill the form and press Find. */
function find(book: string, chapter: string, verse?: string, to?: string) {
  setField('Book', book)
  setField('Chapter', chapter)
  if (verse !== undefined) setField('Verse', verse)
  if (to !== undefined) setField('To', to)
  act(() => {
    screen.getByRole('button', { name: 'Find' }).click()
  })
}

/** Look John 3:16-17 up and wait for it to land in Up Next. */
async function stageJohn316To17() {
  await openSearch()
  find('John', '3', '16', '17')
  await waitFor(() => expect(screen.getByTestId('next-preview')).toHaveTextContent('John 3:16-17'))
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
  stubFetch()
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  socket = null
})

describe('Find Scripture', () => {
  it('keeps the form closed until the operator opens it', () => {
    openWith()

    expect(screen.getByRole('button', { name: 'Open' })).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByLabelText('Chapter')).not.toBeInTheDocument()
    // Nothing asked the server for the book list yet.
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('loads the canonical book list from the server, with the operator token', async () => {
    window.localStorage.setItem('biblelive_operator_token', 'tok-123')
    openWith()
    await openSearch()

    const options = within(screen.getByLabelText('Book')).getAllByRole('option')
    expect(options.map(option => option.textContent)).toEqual(['Genesis', 'John'])

    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/books')
    expect((init as RequestInit).headers).toEqual({ Authorization: 'Bearer tok-123' })
  })

  it('validates a chapter beyond the book before asking the server', async () => {
    openWith()
    await openSearch()

    find('Genesis', '51')

    expect(screen.getByRole('alert')).toHaveTextContent('Genesis has 50 chapters.')
    expect(fetchMock).toHaveBeenCalledTimes(1) // the book list only
    expect(screen.getByText('Nothing staged')).toBeInTheDocument()
  })

  it('validates a reversed verse range', async () => {
    openWith()
    await openSearch()

    find('John', '3', '16', '15')

    expect(screen.getByRole('alert')).toHaveTextContent(
      'The ending verse must be at or after the starting verse.'
    )
    expect(screen.queryByTestId('next-preview')).not.toBeInTheDocument()
  })

  it('stages the looked-up passage in Up Next with its verse text', async () => {
    openWith({ currentDisplay: romans })

    await openSearch()
    find('John', '3', '16', '17')
    await waitFor(() => expect(screen.getByTestId('next-preview')).toBeInTheDocument())

    const staged = within(screen.getByTestId('next-preview'))
    expect(staged.getByText('John 3:16-17')).toBeInTheDocument()
    expect(within(screen.getByTestId('next-verses')).getByText('John 3:16 text')).toBeInTheDocument()
    expect(screen.getByText('Staged: John 3:16-17 — press Show to display')).toBeInTheDocument()

    // The lookup carried the coordinates and nothing else.
    const lookupCall = fetchMock.mock.calls.find(([url]) => String(url).startsWith('/api/lookup'))
    expect(String(lookupCall?.[0])).toBe(
      '/api/lookup?book=John&chapter=3&verse_start=16&verse_end=17'
    )

    // Staging is local: the display is untouched and nothing was sent.
    expect(within(screen.getByTestId('live-verses')).getByText('Romans 8:28 text')).toBeInTheDocument()
    expect(socket?.send).not.toHaveBeenCalled()
  })

  it('never puts a manual passage in the pending queue', async () => {
    openWith({ pending: [romans] })
    await stageJohn316To17()

    // It appears in Up Next and nowhere else: the queue still shows one card.
    expect(screen.getAllByText('John 3:16-17')).toHaveLength(1)
    expect(screen.getAllByText('Romans 8:28')).toHaveLength(1)
    expect(screen.queryByText('Manual')).toBeInTheDocument()
    expect(socket?.send).not.toHaveBeenCalled()
  })

  it('keeps a staged manual passage when a detection arrives', async () => {
    openWith()
    await stageJohn316To17()

    push({ type: 'pending_update', data: { pending: [romans], count: 1 } })

    expect(within(screen.getByTestId('next-preview')).getByText('John 3:16-17')).toBeInTheDocument()
    expect(screen.getByText('Manual')).toBeInTheDocument()
    expect(screen.getByText('Romans 8:28')).toBeInTheDocument()
  })

  it('reports verses the display limit hides', async () => {
    openWith()
    await openSearch()

    find('Genesis', '1', '1', '5')
    await waitFor(() => expect(screen.getByTestId('next-preview')).toBeInTheDocument())

    expect(screen.getByText('+2 more verses hidden by the current limit')).toBeInTheDocument()
  })

  it('sends the passage coordinates on Show and waits for the server to confirm', async () => {
    openWith({ currentDisplay: romans })
    await stageJohn316To17()

    showNext()

    expect(lastSent()).toEqual({
      action: 'display_reference',
      book: 'John',
      chapter: 3,
      verse_start: 16,
      verse_end: 17,
    })
    // Sent is not shown: LIVE still holds what the projector had.
    expect(within(screen.getByTestId('live-verses')).getByText('Romans 8:28 text')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Show' })).toBeDisabled()

    push({ type: 'display_confirmed', data: { reference: manualReference('John', 3, 16, 17) } })

    expect(within(screen.getByTestId('live-verses')).getByText('John 3:16 text')).toBeInTheDocument()
    expect(screen.getByText('Nothing staged')).toBeInTheDocument()
    expect(screen.getByText('Shown: John 3:16-17 (manual)')).toBeInTheDocument()
  })

  it('ignores a second Show while the first is still in flight', async () => {
    openWith()
    await stageJohn316To17()

    showNext()
    showNext()

    expect(socket?.send).toHaveBeenCalledTimes(1)
  })

  it('keeps the passage staged and the display unchanged when Show is rejected', async () => {
    openWith({ currentDisplay: romans })
    await stageJohn316To17()

    showNext()
    push({
      type: 'error',
      data: { action: 'display_reference', message: 'Genesis 1:60 is not in the Bible text' },
    })

    expect(screen.getByRole('alert')).toHaveTextContent(
      'Could not show that passage: Genesis 1:60 is not in the Bible text'
    )
    expect(within(screen.getByTestId('live-verses')).getByText('Romans 8:28 text')).toBeInTheDocument()
    // Still staged, so the operator can fix the passage or retry.
    expect(within(screen.getByTestId('next-preview')).getByText('John 3:16-17')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Show' })).toBeEnabled()
  })

  it('shows the server’s lookup error inline and stages nothing', async () => {
    openWith()
    await openSearch()

    lookupStatus = 404
    lookupErrorDetail = 'Genesis 1:999 is not in the Bible text available on this server.'
    find('Genesis', '1', '999')
    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent('Genesis 1:999 is not in the Bible text')
    )

    expect(screen.queryByTestId('next-preview')).not.toBeInTheDocument()
    expect(socket?.send).not.toHaveBeenCalled()
  })

  it('offers a retry when the book list cannot be loaded', async () => {
    openWith()
    const failing = fetchMock
    failing.mockImplementationOnce(async () => {
      throw new TypeError('Failed to fetch')
    })

    await openSearch().catch(() => undefined)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument())
    expect(screen.getByRole('alert')).toHaveTextContent('Could not reach the server to load the book list.')

    act(() => {
      screen.getByRole('button', { name: 'Retry' }).click()
    })
    await waitFor(() => expect(screen.getByRole('option', { name: 'John' })).toBeInTheDocument())
  })

  it('disables the form while the socket is closed and re-enables it on reconnect', async () => {
    openWith()
    await stageJohn316To17()

    act(() => {
      socket?.onclose?.({ code: 1006 })
    })

    expect(screen.getByText(/Not connected to the server/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Show' })).toBeDisabled()
    expect(screen.getByLabelText('Chapter')).toBeDisabled()

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

    expect(screen.getByRole('button', { name: 'Show' })).toBeEnabled()
    expect(screen.getByLabelText('Chapter')).toBeEnabled()
    // The operator's staged passage survived the drop.
    expect(within(screen.getByTestId('next-preview')).getByText('John 3:16-17')).toBeInTheDocument()
  })

  it('returns to a queue selection when a pending reference is chosen instead', async () => {
    openWith({ pending: [romans] })
    await stageJohn316To17()
    expect(screen.getByText('Manual')).toBeInTheDocument()

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
})
