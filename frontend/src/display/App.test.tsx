import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, render, screen, waitFor } from '@testing-library/react'
import App from './App'
import { DISPLAY_THEME_STORAGE_KEY } from '../shared/verseText'
import type { DisplayMessage } from '../shared/types'

/** The subset of WebSocket the display app uses. */
interface MockSocket {
  onopen: (() => void) | null
  onmessage: ((event: { data: string }) => void) | null
  onclose: ((event: { code: number }) => void) | null
  onerror: ((event: unknown) => void) | null
  readyState: number
  send: (data: string) => void
  close: () => void
}

/** Captured socket instance so tests can push server messages. */
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

/** Deliver a server message to the display app. */
function push(message: DisplayMessage) {
  act(() => {
    socket?.onmessage?.({ data: JSON.stringify(message) })
  })
}

function reference() {
  return {
    id: 'ref-1',
    book: 'John',
    chapter: 1,
    verse_start: 1,
    verse_end: 3,
    confidence: 'high' as const,
    inferred: false,
    text: '',
    verses: [
      { verse: 1, text: 'In the beginning was the Word.' },
      { verse: 2, text: 'The same was in the beginning with God.' },
      { verse: 3, text: 'All things were made by him.' },
    ],
    created_at: '2026-01-01T00:00:00Z',
  }
}

beforeEach(() => {
  window.localStorage.clear()
  document.documentElement.removeAttribute('data-theme')

  // A constructor returning an object replaces `this`, so the test can drive the
  // instance the app actually holds.
  vi.stubGlobal('WebSocket', function MockWebSocket() {
    return createMockSocket()
  })

  // jsdom has no ResizeObserver; the fit effect only needs it to exist.
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe = vi.fn()
      disconnect = vi.fn()
      unobserve = vi.fn()
    }
  )
})

afterEach(() => {
  vi.unstubAllGlobals()
  socket = null
})

describe('display theme', () => {
  it('starts dark before any server message arrives', () => {
    render(<App />)
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark')
  })

  it('applies the theme pushed by the server', async () => {
    render(<App />)

    push({ type: 'display_settings', data: { verse_limit: 3, theme: 'light' } })

    await waitFor(() => expect(document.documentElement.getAttribute('data-theme')).toBe('light'))
    expect(window.localStorage.getItem(DISPLAY_THEME_STORAGE_KEY)).toBe('light')
  })

  it('falls back to dark when the server sends an unknown theme', async () => {
    render(<App />)

    push({
      type: 'display_settings',
      data: { verse_limit: 3, theme: 'sepia' as never },
    })

    await waitFor(() => expect(document.documentElement.getAttribute('data-theme')).toBe('dark'))
  })

  it('toggles on T for the operator at the projector', async () => {
    render(<App />)
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark')

    act(() => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 't' }))
    })
    await waitFor(() => expect(document.documentElement.getAttribute('data-theme')).toBe('light'))

    act(() => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'T' }))
    })
    await waitFor(() => expect(document.documentElement.getAttribute('data-theme')).toBe('dark'))
  })
})

describe('display verses', () => {
  it('shows the passage and the reference once approved', async () => {
    render(<App />)
    act(() => {
      socket?.onopen?.()
    })

    push({ type: 'reference', data: reference() })

    await waitFor(() => {
      const block = screen.getByTestId('display-verses')
      expect(block.textContent).toContain('In the beginning was the Word.')
    })
    // The uppercase treatment is CSS, so assert the rendered text.
    expect(screen.getByText('John 1:1-3')).toBeInTheDocument()
  })

  it('names the book in the spoken language when the server labels it', async () => {
    render(<App />)
    act(() => {
      socket?.onopen?.()
    })

    push({ type: 'reference', data: { ...reference(), book_label: 'Jono evangelija' } })

    await waitFor(() => expect(screen.getByText('Jono evangelija 1:1-3')).toBeInTheDocument())
    expect(screen.queryByText('John 1:1-3')).not.toBeInTheDocument()
  })
})

describe('idle display', () => {
  it('shows only the logo when nothing is on screen, with no status text', () => {
    const { container } = render(<App />)
    act(() => {
      socket?.onopen?.()
    })

    expect(container.querySelector('.display-waiting-icon')).toBeInTheDocument()
    expect(container.textContent).toBe('')
  })

  it('goes back to the logo alone after a clear', async () => {
    const { container } = render(<App />)
    act(() => {
      socket?.onopen?.()
    })
    push({ type: 'reference', data: reference() })
    await waitFor(() => expect(screen.getByText('John 1:1-3')).toBeInTheDocument())

    push({ type: 'clear', data: {} })

    await waitFor(() =>
      expect(container.querySelector('.display-waiting-icon')).toBeInTheDocument()
    )
    expect(container.textContent).toBe('')
  })
})
