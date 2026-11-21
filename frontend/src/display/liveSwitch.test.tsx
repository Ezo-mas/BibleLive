import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { act, render, screen } from '@testing-library/react'
import App from './App'
import type { BibleReference } from '../shared/types'
import { createMockSocket, push } from '../test/operatorHarness'
import type { MockSocket } from '../test/operatorHarness'

let socket: MockSocket | null = null
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

function settings(live?: boolean) {
  push(socket, {
    type: 'display_settings',
    data: {
      verse_limit: 3,
      theme: 'light',
      clear_mode: 'idle',
      idle_image: { version: '0123456789abcdef', fit: 'logo' },
      ...(live === undefined ? {} : { live }),
    },
  })
}

function showPassage() {
  push(socket, { type: 'reference', data: passage })
  act(() => {
    vi.advanceTimersByTime(400)
  })
}

it('draws nothing off air: no passage, no idle picture', () => {
  render(<App />)
  settings(true)
  showPassage()
  expect(screen.getByText(passage.text)).toBeInTheDocument()

  // The server's order: settings first, then a clear.
  settings(false)
  push(socket, { type: 'clear', data: {} })
  act(() => {
    vi.advanceTimersByTime(400)
  })

  const offAir = screen.getByTestId('display-off-air')
  expect(offAir).toBeEmptyDOMElement()
  expect(screen.queryByText(passage.text)).not.toBeInTheDocument()
  expect(screen.queryByTestId('display-idle-image')).not.toBeInTheDocument()
})

it('hides even a passage that arrives while off air', () => {
  render(<App />)
  settings(false)
  showPassage()
  expect(screen.getByTestId('display-off-air')).toBeEmptyDOMElement()
  expect(screen.queryByText(passage.text)).not.toBeInTheDocument()
})

it('back on air shows the passage the server re-sends', () => {
  render(<App />)
  settings(false)
  // The server's order: the passage first, then the settings.
  push(socket, { type: 'reference', data: passage })
  settings(true)
  act(() => {
    vi.advanceTimersByTime(400)
  })
  expect(screen.queryByTestId('display-off-air')).not.toBeInTheDocument()
  expect(screen.getByText(passage.text)).toBeInTheDocument()
})

it('back on air shows the passage at once, never the idle picture first', () => {
  render(<App />)
  settings(true)
  showPassage()
  settings(false)
  push(socket, { type: 'clear', data: {} })
  push(socket, { type: 'reference', data: passage })
  settings(true)
  // No timer has run: the first paint back on air is already the passage.
  expect(screen.queryByTestId('display-idle-image')).not.toBeInTheDocument()
  expect(screen.getByText(passage.text)).toBeInTheDocument()
})

it('treats settings without the field, from an older server, as live', () => {
  render(<App />)
  settings(false)
  settings()
  expect(screen.queryByTestId('display-off-air')).not.toBeInTheDocument()
  expect(screen.getByTestId('display-idle-image')).toBeInTheDocument()
})
