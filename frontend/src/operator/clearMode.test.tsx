import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, within } from '@testing-library/react'
import App from './App'
import type { ClearMode } from '../shared/types'
import { createMockSocket, lastSent, push } from '../test/operatorHarness'
import type { MockSocket } from '../test/operatorHarness'

let socket: MockSocket | null = null

beforeEach(() => {
  window.localStorage.clear()
  vi.stubGlobal(
    'WebSocket',
    Object.assign(
      function () {
        socket = createMockSocket()
        return socket
      },
      { OPEN: 1, CLOSING: 2, CLOSED: 3 }
    )
  )
  vi.stubGlobal('matchMedia', () => ({
    matches: false,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  }))
  vi.spyOn(console, 'error').mockImplementation(() => {})
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  socket = null
})

function open(clear_mode: ClearMode = 'idle') {
  render(<App />)
  act(() => socket?.onopen?.())
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
      display_settings: { verse_limit: 3, theme: 'dark', clear_mode },
    },
  })
  fireEvent.click(screen.getByRole('button', { name: 'Settings' }))
  return within(screen.getByRole('group', { name: 'When the display is cleared' }))
}

function confirm(clear_mode: ClearMode) {
  push(socket, {
    type: 'display_settings_confirmed',
    data: { verse_limit: 3, theme: 'dark', clear_mode },
  })
}

it('sends the selected mode optimistically, confirms it and reverts a later error', () => {
  const row = open()
  const idle = row.getByRole('button', { name: 'Idle picture' })
  const blank = row.getByRole('button', { name: 'Blank' })
  expect(idle).toHaveAttribute('aria-pressed', 'true')
  fireEvent.click(blank)
  expect(lastSent(socket)).toEqual({ action: 'set_clear_mode', clear_mode: 'blank' })
  expect(blank).toHaveAttribute('aria-pressed', 'true')
  confirm('blank')
  expect(screen.queryByText(/when cleared/)).not.toBeInTheDocument()
  fireEvent.click(idle)
  expect(lastSent(socket)).toEqual({ action: 'set_clear_mode', clear_mode: 'idle' })
  expect(idle).toHaveAttribute('aria-pressed', 'true')
  push(socket, { type: 'error', data: { action: 'set_clear_mode', message: 'Refused' } })
  expect(blank).toHaveAttribute('aria-pressed', 'true')
  expect(screen.getByRole('alert')).toHaveTextContent('reverted')
})

it('reflects snapshot and other-console confirmations and disables offline choices', () => {
  const row = open('blank')
  expect(row.getByRole('button', { name: 'Blank' })).toHaveAttribute('aria-pressed', 'true')
  confirm('idle')
  expect(row.getByRole('button', { name: 'Idle picture' })).toHaveAttribute('aria-pressed', 'true')
  act(() => socket?.onclose?.({ code: 1006 }))
  expect(row.getByRole('button', { name: 'Blank' })).toBeDisabled()
  expect(row.getByRole('button', { name: 'Idle picture' })).toBeDisabled()
})

const passage = {
  id: 'passage-1',
  book: 'John',
  chapter: 3,
  verse_start: 16,
  verse_end: 16,
  confidence: 'high' as const,
  inferred: false,
  text: 'For God so loved the world.',
  verses: [{ verse: 16, text: 'For God so loved the world.' }],
  created_at: '2026-01-01T00:00:00Z',
}
const picture = { version: '0123456789abcdef', fit: 'logo' as const }

function snapshot(current_display: typeof passage | null, clear_mode: ClearMode = 'blank') {
  push(socket, {
    type: 'state_snapshot',
    data: {
      current_display,
      pending: [],
      history: [],
      pending_count: 0,
      history_count: 0,
      display_connections: 1,
      operator_connections: 1,
      display_settings: { verse_limit: 3, theme: 'dark', clear_mode, idle_image: picture },
    },
  })
}

function confirmWithPicture(clear_mode: ClearMode) {
  push(socket, {
    type: 'display_settings_confirmed',
    data: { verse_limit: 3, theme: 'dark', clear_mode, idle_image: picture },
  })
}

it.each(['approval_confirmed', 'display_confirmed'] as const)(
  'updates the cleared preview after %s and follows mode switches',
  type => {
    render(<App />)
    act(() => socket?.onopen?.())
    snapshot(null)
    expect(screen.getByTestId('live-idle-picture')).toBeInTheDocument()

    push(socket, { type, data: { reference: passage } })
    push(socket, { type: 'clear_confirmed', data: {} })
    expect(screen.queryByTestId('live-idle-picture')).not.toBeInTheDocument()
    confirmWithPicture('idle')
    expect(screen.getByTestId('live-idle-picture')).toBeInTheDocument()
    confirmWithPicture('blank')
    expect(screen.queryByTestId('live-idle-picture')).not.toBeInTheDocument()
    snapshot(null)
    expect(screen.queryByTestId('live-idle-picture')).not.toBeInTheDocument()
  }
)

it('tracks a passage from a snapshot and resets after the console reloads', () => {
  const first = render(<App />)
  act(() => socket?.onopen?.())
  snapshot(passage)
  push(socket, { type: 'clear_confirmed', data: {} })
  expect(screen.queryByTestId('live-idle-picture')).not.toBeInTheDocument()
  first.unmount()

  render(<App />)
  act(() => socket?.onopen?.())
  snapshot(null)
  expect(screen.getByTestId('live-idle-picture')).toBeInTheDocument()
})
