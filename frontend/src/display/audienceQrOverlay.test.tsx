import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { act, render, screen } from '@testing-library/react'
import App, { LIVE_FADE_FALLBACK_MS } from './App'
import type { BibleReference } from '../shared/types'
import { createMockSocket, push } from '../test/operatorHarness'
import type { MockSocket } from '../test/operatorHarness'

let socket: MockSocket | null = null
const svg = 'data:image/svg+xml;charset=utf-8,%3Csvg%3E%3C%2Fsvg%3E'
const url = 'http://192.168.1.23:8000/display'
const passage: BibleReference = {
  id: 'ref-1',
  book: 'John',
  chapter: 3,
  verse_start: 16,
  verse_end: 16,
  confidence: 'high',
  inferred: false,
  text: 'For God so loved the world.',
  verses: [{ verse: 16, text: 'For God so loved the world.' }],
  created_at: '2026-01-01T00:00:00Z',
}

beforeEach(() => {
  vi.useFakeTimers()
  window.localStorage.clear()
  vi.stubGlobal('WebSocket', function () {
    socket = createMockSocket()
    return socket
  })
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe = vi.fn()
      disconnect = vi.fn()
    }
  )
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
  socket = null
})

function settings(live: boolean) {
  push(socket, {
    type: 'display_settings',
    data: { verse_limit: 3, theme: 'dark', clear_mode: 'idle', live },
  })
}

it('shows the code beside the passage and hides it again', () => {
  const { container } = render(<App />)
  settings(true)
  push(socket, { type: 'reference', data: passage })
  act(() => {
    vi.advanceTimersByTime(400)
  })

  push(socket, { type: 'audience_qr', data: { url, svg } })
  const code = screen.getByTestId('display-audience-qr')
  expect(code.querySelector('img')).toHaveAttribute('src', svg)
  expect(code).toHaveTextContent('192.168.1.23:8000/display')
  // The passage stays: the code is beside it, not instead of it.
  expect(screen.getByText(passage.text)).toBeInTheDocument()
  expect(container.firstChild).toHaveClass('display-app--with-qr')

  push(socket, { type: 'audience_qr', data: { url: null, svg: null } })
  expect(screen.queryByTestId('display-audience-qr')).not.toBeInTheDocument()
  expect(container.firstChild).not.toHaveClass('display-app--with-qr')
  expect(screen.getByText(passage.text)).toBeInTheDocument()
})

it('goes dark off air and comes back on air', () => {
  render(<App />)
  settings(true)
  push(socket, { type: 'audience_qr', data: { url, svg } })
  expect(screen.getByTestId('display-audience-qr')).toBeInTheDocument()

  settings(false)
  // The screen fades to black first, the code with it.
  expect(screen.getByTestId('display-off-air-cover')).toBeInTheDocument()
  act(() => {
    vi.advanceTimersByTime(LIVE_FADE_FALLBACK_MS)
  })
  expect(screen.getByTestId('display-off-air')).toBeEmptyDOMElement()

  settings(true)
  expect(screen.getByTestId('display-audience-qr')).toBeInTheDocument()
})
