import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { act, render, screen } from '@testing-library/react'
import App from './App'
import type { BibleReference, ClearMode } from '../shared/types'
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

function settings(clear_mode: ClearMode = 'blank') {
  push(socket, {
    type: 'display_settings',
    data: {
      verse_limit: 3,
      theme: 'dark',
      clear_mode,
      idle_image: { version: '0123456789abcdef', fit: 'logo' },
    },
  })
}

function showPassage() {
  push(socket, { type: 'reference', data: passage })
  act(() => vi.advanceTimersByTime(400))
  expect(screen.getByText(passage.text)).toBeInTheDocument()
}

function clear() {
  push(socket, { type: 'clear', data: {} })
  act(() => vi.advanceTimersByTime(400))
}

it('shows the initial picture in blank mode, including an empty connect-time state', () => {
  render(<App />)
  clear()
  settings()
  expect(screen.getByTestId('display-idle-image')).toBeInTheDocument()
  clear()
  expect(screen.getByTestId('display-idle-image')).toBeInTheDocument()
})

it.each(['idle', 'blank'] as const)('uses %s mode after a passage is cleared', mode => {
  render(<App />)
  settings(mode)
  showPassage()
  clear()
  expect(screen.queryByText(passage.text)).not.toBeInTheDocument()
  expect(screen.queryByTestId('display-idle-image') !== null).toBe(mode === 'idle')
})

it('applies a mode switch immediately on a cleared screen and keeps passages visible', () => {
  render(<App />)
  settings('idle')
  showPassage()
  clear()
  settings('blank')
  expect(screen.queryByTestId('display-idle-image')).not.toBeInTheDocument()
  settings('idle')
  expect(screen.getByTestId('display-idle-image')).toBeInTheDocument()
  showPassage()
  settings('blank')
  expect(screen.getByText(passage.text)).toBeInTheDocument()
  clear()
  expect(screen.queryByTestId('display-idle-image')).not.toBeInTheDocument()
})

it('shows the picture again after reload until the next passage', () => {
  const first = render(<App />)
  settings()
  showPassage()
  clear()
  first.unmount()
  render(<App />)
  clear()
  settings()
  expect(screen.getByTestId('display-idle-image')).toBeInTheDocument()
  showPassage()
  clear()
  expect(screen.queryByTestId('display-idle-image')).not.toBeInTheDocument()
})
