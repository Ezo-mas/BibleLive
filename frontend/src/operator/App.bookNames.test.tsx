import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Mock } from 'vitest'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import App from './App'
import type { BibleReference } from '../shared/types'
import { createMockSocket, jsonResponse, push } from '../test/operatorHarness'
import type { MockSocket } from '../test/operatorHarness'

/**
 * Book names follow the spoken language: Lithuanian labels for what the server
 * sends and what the search lists, canonical names for everything it is asked.
 */

let socket: MockSocket | null = null

const MockWebSocket = Object.assign(
  function MockWebSocketStub() {
    socket = createMockSocket()
    return socket
  },
  { OPEN: 1, CLOSING: 2, CLOSED: 3 }
)

function sentActions() {
  const calls = socket?.send.mock.calls ?? []
  return calls
    .map(([text]) => JSON.parse(text))
    .filter(message => message.action !== 'authenticate')
}

/** A slice of the canon as /api/books serves it while KJV is loaded: no names of its own. */
const ENGLISH_BOOKS = {
  books: [
    { name: 'Genesis', chapters: 50, order: 1, abbrev: 'gn', variants: ['genesis'] },
    { name: 'John', chapters: 21, order: 43, abbrev: 'jn', variants: ['john'] },
  ],
}

/** The same slice while the Lithuanian LTB is loaded: its own names (real ones). */
const LITHUANIAN_BOOKS = {
  books: [
    {
      ...ENGLISH_BOOKS.books[0],
      local_name: 'Pradžios',
      local_variants: ['pradz knyga', 'pradz'],
    },
    {
      ...ENGLISH_BOOKS.books[1],
      local_name: 'Jono evangelija',
      local_variants: ['jono', 'jonas'],
    },
  ],
}

function reference(overrides: Partial<BibleReference> = {}): BibleReference {
  return {
    id: 'p-1',
    book: 'John',
    chapter: 3,
    verse_start: 16,
    verse_end: 16,
    confidence: 'high',
    inferred: false,
    text: 'text',
    verses: [{ verse: 16, text: 'text' }],
    created_at: '2026-01-01T00:00:00Z',
    ...overrides,
  }
}

/** The Bible translation the server has loaded; /api/books answers for it. */
let translation = 'ltb'
let fetchMock: Mock<(input: RequestInfo | URL) => Promise<Response>>

function stubFetch() {
  fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    // The spoken language is deliberately irrelevant to book names.
    if (url.startsWith('/api/language')) return jsonResponse({ language: 'en' })
    if (url.startsWith('/api/bible-version')) {
      return jsonResponse({ version: translation, available: ['kjv', 'ltb'] })
    }
    if (url.startsWith('/api/pipeline/')) {
      return jsonResponse({
        status: 'unknown',
        language: null,
        problems: [],
        seconds_since_report: null,
      })
    }
    if (url.startsWith('/api/books')) {
      return jsonResponse(translation === 'ltb' ? LITHUANIAN_BOOKS : ENGLISH_BOOKS)
    }
    const params = new URLSearchParams(url.split('?')[1] ?? '')
    const book = params.get('book') ?? ''
    return jsonResponse({
      book,
      chapter: Number(params.get('chapter')),
      verse_start: Number(params.get('verse_start') ?? '1'),
      verse_end: Number(params.get('verse_start') ?? '1'),
      verses: [{ verse: Number(params.get('verse_start') ?? '1'), text: 'verse text' }],
      text: 'verse text',
    })
  })
  vi.stubGlobal('fetch', fetchMock)
}

function openWith(
  options: { currentDisplay?: BibleReference | null; pending?: BibleReference[] } = {}
) {
  const { currentDisplay = null, pending = [] } = options
  render(<App />)
  act(() => {
    socket?.onopen?.()
  })
  push(socket, {
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

function searchBox() {
  return screen.getByLabelText('Find scripture by book or reference')
}

function type(text: string) {
  fireEvent.change(searchBox(), { target: { value: text } })
}

function results() {
  return within(screen.getByRole('list', { name: 'Scripture search results' }))
}

/** How many times the console has asked for the book list. */
function bookListRequests() {
  return fetchMock.mock.calls.filter(([url]) => String(url).startsWith('/api/books')).length
}

/** The console has read which translation is loaded (it does so once the socket is up). */
async function versionKnown() {
  await waitFor(() =>
    expect(fetchMock.mock.calls.some(([url]) => String(url).startsWith('/api/bible-version'))).toBe(
      true
    )
  )
  await act(async () => {})
}

beforeEach(() => {
  window.localStorage.clear()
  translation = 'ltb'
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
  stubFetch()
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  socket = null
})

describe('book names from the loaded Bible translation', () => {
  it('shows the server-sent label for the live display and the queue', () => {
    openWith({
      currentDisplay: reference({ id: 'live', book_label: 'Jono evangelija' }),
      pending: [reference({ id: 'p-2', book_label: 'Pradžios', book: 'Genesis', chapter: 1 })],
    })

    expect(screen.getByText('Jono evangelija 3:16')).toBeInTheDocument()
    expect(screen.getByText('Pradžios 1:16')).toBeInTheDocument()
    expect(screen.queryByText('John 3:16')).not.toBeInTheDocument()
  })

  it('shows the canonical name when the server sent no label', () => {
    openWith({ currentDisplay: reference({ id: 'live' }) })

    expect(screen.getByText('John 3:16')).toBeInTheDocument()
  })

  it('lists search results by the translation names and finds them without accents', async () => {
    openWith()

    type('pradz')
    await waitFor(() =>
      expect(results().getByRole('button', { name: /Pradžios/ })).toBeInTheDocument()
    )
    expect(results().queryByRole('button', { name: /Genesis/ })).not.toBeInTheDocument()
  })

  it('stages a search in the translation names but asks the server by canonical name', async () => {
    openWith()

    type('pradzios 1:3')
    await waitFor(() => expect(results().getByText('Pradžios 1:3')).toBeInTheDocument())
    act(() => {
      fireEvent.keyDown(searchBox(), { key: 'Enter', ctrlKey: true })
    })

    // The lookup named the canonical book...
    await waitFor(() => {
      const lookup = fetchMock.mock.calls.find(([url]) => String(url).startsWith('/api/lookup'))
      expect(lookup).toBeDefined()
      expect(String(lookup?.[0])).toContain('book=Genesis')
    })
    // ...while the staged passage reads in the translation's name.
    await waitFor(() =>
      expect(screen.getByRole('heading', { name: 'Pradžios 1:3' })).toBeInTheDocument()
    )

    act(() => {
      screen.getByRole('button', { name: 'Show on display' }).click()
    })
    expect(sentActions().at(-1)).toMatchObject({ action: 'display_reference', book: 'Genesis' })
  })

  it('keeps English names in the search while an English translation is loaded', async () => {
    translation = 'kjv'
    openWith()

    type('gen')
    await waitFor(() =>
      expect(results().getByRole('button', { name: /Genesis/ })).toBeInTheDocument()
    )
    expect(results().queryByRole('button', { name: /Pradžios/ })).not.toBeInTheDocument()
  })

  it('does not depend on the spoken language', async () => {
    // The pipeline says English, but the loaded translation is Lithuanian.
    openWith()

    type('jono')
    await waitFor(() =>
      expect(results().getByRole('button', { name: /Jono evangelija/ })).toBeInTheDocument()
    )
  })

  it('reloads the book list when the translation is switched', async () => {
    translation = 'kjv'
    openWith()
    await versionKnown()

    type('gen')
    await waitFor(() =>
      expect(results().getByRole('button', { name: /Genesis/ })).toBeInTheDocument()
    )
    const requestsBefore = bookListRequests()

    // Another operator (or this one) switches to the Lithuanian translation.
    translation = 'ltb'
    push(socket, {
      type: 'bible_version_confirmed',
      data: { version: 'ltb', available: ['kjv', 'ltb'] },
    })

    await waitFor(() => expect(bookListRequests()).toBe(requestsBefore + 1))
    type('pradz')
    await waitFor(() =>
      expect(results().getByRole('button', { name: /Pradžios/ })).toBeInTheDocument()
    )
  })

  it('does not fetch the book list just because the first version was learned', async () => {
    openWith()
    await versionKnown()

    expect(bookListRequests()).toBe(0)
  })
})
