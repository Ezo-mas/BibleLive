import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, render, screen, waitFor } from '@testing-library/react'
import StreamOverlay from './StreamOverlay'
import type { DisplayMessage } from '../shared/types'

interface MockSocket {
  onopen: (() => void) | null
  onmessage: ((event: { data: string }) => void) | null
  onclose: ((event: { code: number }) => void) | null
  onerror: ((event: unknown) => void) | null
  readyState: number
  send: (data: string) => void
  close: () => void
}

let socket: MockSocket | null = null

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
    verse_end: 4,
    confidence: 'high' as const,
    inferred: false,
    text: '',
    verses: [
      { verse: 1, text: 'In the beginning was the Word.' },
      { verse: 2, text: 'The same was in the beginning with God.' },
      { verse: 3, text: 'All things were made by him.' },
      { verse: 4, text: 'In him was life.' },
    ],
    created_at: '2026-01-01T00:00:00Z',
  }
}

beforeEach(() => {
  window.localStorage.clear()
  window.history.replaceState(null, '', '/display?mode=obs&height=40')
  vi.stubGlobal('WebSocket', function MockWebSocket() {
    socket = {
      onopen: null,
      onmessage: null,
      onclose: null,
      onerror: null,
      readyState: 1,
      send: vi.fn(),
      close: vi.fn(),
    }
    return socket
  })
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
  window.history.replaceState(null, '', '/')
})

describe('stream overlay', () => {
  it('leaves the page transparent and shows nothing before a passage', () => {
    render(<StreamOverlay />)
    expect(document.documentElement.classList.contains('stream-overlay-page')).toBe(true)
    expect(screen.queryByTestId('stream-band')).toBeNull()
  })

  it('shows the passage on a band of the height from the URL', async () => {
    render(<StreamOverlay />)
    push({ type: 'display_settings', data: { verse_limit: 'all', theme: 'dark' } })
    push({ type: 'reference', data: reference() })

    const band = await screen.findByTestId('stream-band')
    await waitFor(() => expect(band.className).toContain('stream-band--visible'))
    expect(band.className).toContain('stream-band--bottom')
    expect(band.style.height).toBe('40vh')
    expect(screen.getByText('In the beginning was the Word.')).toBeInTheDocument()
    expect(screen.getByText('In him was life.')).toBeInTheDocument()
  })

  it("follows the projector's verse limit and says how many are left out", async () => {
    render(<StreamOverlay />)
    push({ type: 'display_settings', data: { verse_limit: 3, theme: 'dark' } })
    push({ type: 'reference', data: reference() })

    await screen.findByText('All things were made by him.')
    expect(screen.queryByText('In him was life.')).toBeNull()
    expect(screen.getByText(/\+1 more verse$/)).toBeInTheDocument()
  })

  it('fades out and removes the band when the projector is cleared', async () => {
    render(<StreamOverlay />)
    push({ type: 'reference', data: reference() })
    await screen.findByTestId('stream-band')

    push({ type: 'clear', data: {} })
    await waitFor(() => expect(screen.queryByTestId('stream-band')).toBeNull())
  })

  it('never shows the idle picture, even when the server sends one (it sits over a camera)', async () => {
    const { container } = render(<StreamOverlay />)
    push({
      type: 'display_settings',
      data: {
        verse_limit: 3,
        theme: 'dark',
        idle_image: { version: '0123456789abcdef', fit: 'fill' },
      },
    })
    expect(container.querySelector('img')).toBeNull()
    expect(container.textContent).toBe('')

    push({ type: 'reference', data: reference() })
    await screen.findByTestId('stream-band')
    push({ type: 'clear', data: {} })
    await waitFor(() => expect(screen.queryByTestId('stream-band')).toBeNull())

    expect(container.querySelector('img')).toBeNull()
    expect(container.textContent).toBe('')
  })
})
