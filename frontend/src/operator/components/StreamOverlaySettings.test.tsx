import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, within } from '@testing-library/react'
import App from '../App'
import { DEFAULT_STREAM_OVERLAY_OPTIONS } from '../../display/streamOverlayOptions'
import { createMockSocket, lastSent, push } from '../../test/operatorHarness'
import type { MockSocket } from '../../test/operatorHarness'

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
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe = vi.fn()
      disconnect = vi.fn()
    }
  )
  // jsdom lays nothing out; give the preview a width so the scaled page is drawn.
  vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(480)
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  socket = null
})

it('sends the whole layout and updates the preview while keeping the OBS link fixed', () => {
  const stream_overlay = { ...DEFAULT_STREAM_OVERLAY_OPTIONS, height_pct: 40 }
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
      display_settings: { verse_limit: 3, theme: 'dark', stream_overlay },
    },
  })
  fireEvent.click(screen.getByRole('button', { name: 'Settings' }))
  const position = within(screen.getByRole('group', { name: 'Position' }))
  const link = screen.getByRole('textbox', { name: 'OBS link' })
  const preview = screen.getByTitle('Preview of the stream overlay')
  const fixedLink = window.location.origin + '/display?mode=obs'
  expect(link).toHaveValue(fixedLink)
  expect(preview).toHaveAttribute('src', '/display?mode=obs&height=40&preview=1')
  expect(preview.style.transform).toBe('scale(0.25)')

  fireEvent.click(position.getByRole('button', { name: 'Top' }))
  expect(lastSent(socket)).toEqual({
    action: 'set_stream_overlay',
    stream_overlay: { ...stream_overlay, position: 'top' },
  })

  push(socket, {
    type: 'display_settings_confirmed',
    data: {
      verse_limit: 3,
      theme: 'dark',
      stream_overlay: { ...stream_overlay, position: 'top', font: 'sans' },
    },
  })
  expect(position.getByRole('button', { name: 'Top' })).toHaveAttribute('aria-pressed', 'true')
  expect(preview).toHaveAttribute(
    'src',
    '/display?mode=obs&height=40&position=top&font=sans&preview=1'
  )
  expect(link).toHaveValue(fixedLink)
})
