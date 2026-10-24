import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import App from './App'
import { createMockSocket, jsonResponse, push } from '../test/operatorHarness'
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

const BOOKS = {
  books: [
    { name: 'Genesis', chapters: 50, order: 1, abbrev: 'gn', variants: ['genesis'] },
    { name: 'John', chapters: 21, order: 43, abbrev: 'jn', variants: ['john'] },
  ],
}

/** The lookup's answer, when it is not the passage itself (a refusal, or none yet). */
let lookupStatus = 200
let lookupDetail = ''
/** When set, lookups wait until the test releases them. */
let deferLookups = false
let releases: Array<() => void> = []

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
  return { book, chapter, verse_start: start, verse_end: end, verses, text: verses[0]?.text }
}

function answerLookup(url: string): Response {
  if (lookupStatus !== 200) return jsonResponse({ detail: lookupDetail }, lookupStatus)
  return jsonResponse(passageBody(url))
}

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
    if (!deferLookups) return answerLookup(url)
    return new Promise<Response>(resolve => {
      releases.push(() => resolve(answerLookup(url)))
    })
  })
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

function openWith() {
  render(<App />)
  act(() => {
    socket?.onopen?.()
  })
  push(socket, {
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
}

function searchBox() {
  return screen.getByLabelText('Find scripture by book or reference')
}

/** Type into the search box (the results arrive after the debounce). */
function type(text: string) {
  fireEvent.change(searchBox(), { target: { value: text } })
}

/** The passage row for a complete reference, once the debounced match has shown it. */
async function passageRow(name: string) {
  await waitFor(() => expect(screen.getByRole('button', { name })).toBeInTheDocument())
  return screen.getByRole('button', { name })
}

beforeEach(() => {
  window.localStorage.clear()
  lookupStatus = 200
  lookupDetail = ''
  deferLookups = false
  releases = []
  vi.stubGlobal('WebSocket', MockWebSocket)
  vi.stubGlobal('fetch', createFetchMock())
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

describe('Find Scripture preview', () => {
  it('shows how the first verse begins under a complete reference', async () => {
    openWith()

    type('john 3:16-17')

    const row = await passageRow('John 3:16-17 — stage in Up Next')
    await waitFor(() => expect(within(row).getByText('John 3:16 text')).toBeInTheDocument())
    expect(row.querySelector('.operator-search-result-preview')).toHaveTextContent('John 3:16 text')
    // The read-only lookup asks for the reference as typed, and nothing else happens.
    expect(vi.mocked(fetch).mock.calls.map(([url]) => String(url))).toContain(
      '/api/lookup?book=John&chapter=3&verse_start=16&verse_end=17'
    )
    expect(screen.queryByTestId('next-preview')).not.toBeInTheDocument()
  })

  it('says it is loading until the verse has arrived', async () => {
    openWith()
    deferLookups = true

    type('john 3:16')

    const row = await passageRow('John 3:16 — stage in Up Next')
    await waitFor(() => expect(within(row).getByText('Loading the verse…')).toBeInTheDocument())

    await releaseLookups()

    await waitFor(() => expect(within(row).getByText('John 3:16 text')).toBeInTheDocument())
  })

  it('shows no preview for a book row', async () => {
    openWith()

    type('gen')

    await waitFor(() =>
      expect(screen.getByRole('button', { name: /^Genesis/ })).toBeInTheDocument()
    )
    expect(document.querySelector('.operator-search-result-preview')).toBeNull()
  })

  it("shows a refused preview's reason inline, and no preview text", async () => {
    openWith()
    lookupStatus = 404
    lookupDetail = 'John 3:999 is not in the Bible text available on this server.'

    type('john 3:999')

    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent(
        'John 3:999 is not in the Bible text available on this server.'
      )
    )
    expect(document.querySelector('.operator-search-result-preview')).toBeNull()
  })
})
