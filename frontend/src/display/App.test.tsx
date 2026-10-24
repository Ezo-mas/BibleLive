import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import App from './App'
import { DISPLAY_THEME_STORAGE_KEY } from '../shared/verseText'
import type { DisplayMessage } from '../shared/types'
import { createMockSocket, push } from '../test/operatorHarness'
import type { MockSocket } from '../test/operatorHarness'

/** Captured socket instance so tests can push server messages. */
let socket: MockSocket | null = null

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
    socket = createMockSocket()
    return socket
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

    push(socket, { type: 'display_settings', data: { verse_limit: 3, theme: 'light' } })

    await waitFor(() => expect(document.documentElement.getAttribute('data-theme')).toBe('light'))
    expect(window.localStorage.getItem(DISPLAY_THEME_STORAGE_KEY)).toBe('light')
  })

  it('falls back to dark when the server sends an unknown theme', async () => {
    render(<App />)

    push(socket, {
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

    push(socket, { type: 'reference', data: reference() })

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

    push(socket, { type: 'reference', data: { ...reference(), book_label: 'Jono evangelija' } })

    await waitFor(() => expect(screen.getByText('Jono evangelija 1:1-3')).toBeInTheDocument())
    expect(screen.queryByText('John 1:1-3')).not.toBeInTheDocument()
  })
})

/**
 * A display_settings message carrying the idle picture. The value is `unknown` so the
 * tests can also push malformed ones, which the typed `idle_image` field would reject.
 */
function pushIdlePicture(picture: unknown) {
  push(socket, {
    type: 'display_settings',
    data: { verse_limit: 3, theme: 'dark', idle_image: picture },
  } as unknown as DisplayMessage)
}

const PICTURE = { version: '0123456789abcdef', fit: 'logo' }

describe('idle display', () => {
  it('is a blank themed screen by default: no book icon, no text, no picture', () => {
    const { container } = render(<App />)
    act(() => {
      socket?.onopen?.()
    })

    expect(container.textContent).toBe('')
    expect(container.querySelector('svg')).toBeNull()
    expect(container.querySelector('img')).toBeNull()
  })

  it('is blank again after a clear, with no picture set', async () => {
    const { container } = render(<App />)
    act(() => {
      socket?.onopen?.()
    })
    push(socket, { type: 'reference', data: reference() })
    await waitFor(() => expect(screen.getByText('John 1:1-3')).toBeInTheDocument())

    push(socket, { type: 'clear', data: {} })

    await waitFor(() => expect(screen.queryByText('John 1:1-3')).not.toBeInTheDocument())
    expect(container.textContent).toBe('')
    expect(container.querySelector('img')).toBeNull()
  })

  it('shows the operator picture, contained, while nothing is on screen', () => {
    render(<App />)
    act(() => {
      socket?.onopen?.()
    })

    pushIdlePicture(PICTURE)

    const picture = screen.getByTestId('display-idle-image')
    expect(picture).toHaveAttribute('src', '/api/idle-screen/image?v=0123456789abcdef')
    expect(picture).toHaveClass('display-idle-image--logo')
    expect(picture).toHaveAttribute('alt', 'Idle screen picture')
  })

  it('falls back to a blank screen when the picture cannot be loaded, until a new upload', () => {
    const { container } = render(<App />)
    act(() => {
      socket?.onopen?.()
    })
    pushIdlePicture(PICTURE)

    // The server answers 404 (the file is gone): no broken-image icon on the projector.
    fireEvent.error(screen.getByTestId('display-idle-image'))
    expect(container.querySelector('img')).toBeNull()

    // The same version coming again (a reconnect) does not retry the broken file.
    pushIdlePicture(PICTURE)
    expect(container.querySelector('img')).toBeNull()

    // A new upload is a new version, so it is tried.
    pushIdlePicture({ version: 'fedcba9876543210', fit: 'logo' })
    expect(screen.getByTestId('display-idle-image')).toHaveAttribute(
      'src',
      '/api/idle-screen/image?v=fedcba9876543210'
    )
  })

  it('fills the screen in fill mode', () => {
    render(<App />)

    pushIdlePicture({ ...PICTURE, fit: 'fill' })

    expect(screen.getByTestId('display-idle-image')).toHaveClass('display-idle-image--fill')
  })

  it('is removed the moment a reference is shown, and comes back after a clear', async () => {
    render(<App />)
    act(() => {
      socket?.onopen?.()
    })
    pushIdlePicture(PICTURE)
    expect(screen.getByTestId('display-idle-image')).toBeInTheDocument()

    push(socket, { type: 'reference', data: reference() })
    await waitFor(() => expect(screen.getByText('John 1:1-3')).toBeInTheDocument())
    expect(screen.queryByTestId('display-idle-image')).not.toBeInTheDocument()

    push(socket, { type: 'clear', data: {} })
    await waitFor(() => expect(screen.getByTestId('display-idle-image')).toBeInTheDocument())
  })

  it('waits for the clear when a picture arrives over a passage on screen', async () => {
    render(<App />)
    act(() => {
      socket?.onopen?.()
    })
    push(socket, { type: 'reference', data: reference() })
    await waitFor(() => expect(screen.getByText('John 1:1-3')).toBeInTheDocument())

    pushIdlePicture(PICTURE)
    expect(screen.queryByTestId('display-idle-image')).not.toBeInTheDocument()

    push(socket, { type: 'clear', data: {} })
    await waitFor(() => expect(screen.getByTestId('display-idle-image')).toBeInTheDocument())
  })

  it('is removed when the server reports no picture', () => {
    render(<App />)
    pushIdlePicture(PICTURE)

    pushIdlePicture(null)

    expect(screen.queryByTestId('display-idle-image')).not.toBeInTheDocument()
  })

  it('stays blank for a server that does not send the field (older versions)', () => {
    const { container } = render(<App />)
    act(() => {
      socket?.onopen?.()
    })

    push(socket, { type: 'display_settings', data: { verse_limit: 3, theme: 'light' } })

    expect(container.querySelector('img')).toBeNull()
    expect(container.textContent).toBe('')
  })

  it.each([
    ['an unknown fit', { ...PICTURE, fit: 'stretch' }],
    ['a version that is not a tag', { ...PICTURE, version: '../../etc/passwd' }],
    ['a missing version', { fit: 'logo' }],
    ['a string', 'idle_screen.png'],
  ])('stays blank for a malformed picture (%s)', (_label, picture) => {
    render(<App />)

    pushIdlePicture(picture)

    expect(screen.queryByTestId('display-idle-image')).not.toBeInTheDocument()
  })
})
