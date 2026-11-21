import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen } from '@testing-library/react'
import App from './App'
import type { BibleReference, DisplaySettings } from '../shared/types'
import { createMockSocket, push } from '../test/operatorHarness'
import type { MockSocket } from '../test/operatorHarness'

let socket: MockSocket | null = null

const MockWebSocket = Object.assign(
  function MockWebSocketStub() {
    socket = createMockSocket()
    return socket
  },
  { OPEN: 1, CLOSING: 2, CLOSED: 3 }
)

const passage: BibleReference = {
  id: 'ref-1',
  book: 'John',
  chapter: 3,
  verse_start: 16,
  verse_end: 16,
  confidence: null,
  source: 'manual',
  inferred: false,
  text: 'For God so loved the world.',
  verses: [{ verse: 16, text: 'For God so loved the world.' }],
  created_at: '2026-01-01T00:00:00Z',
}

function sentMessages() {
  const send = socket?.send as unknown as ReturnType<typeof vi.fn>
  return send.mock.calls.map(call => JSON.parse(call[0] as string))
}

function settings(live?: boolean): DisplaySettings {
  return { verse_limit: 3, theme: 'dark', ...(live === undefined ? {} : { live }) }
}

function openWith(display_settings: DisplaySettings) {
  render(<App />)
  act(() => {
    socket?.onopen?.()
  })
  push(socket, {
    type: 'state_snapshot',
    data: {
      current_display: passage,
      pending: [],
      history: [],
      pending_count: 0,
      history_count: 0,
      display_connections: 1,
      operator_connections: 1,
      display_settings,
      auto_mode: 'off',
    },
  })
}

/** The monitor frame, marked off air while the display is (the preview is dimmed). */
function ring() {
  return document.querySelector('.operator-monitor-wrap')
}

function liveSwitch() {
  // One button whose name follows its label: Live on air, Off air blacked out.
  return screen.getByRole('button', { name: /^(Live|Off air)$/ })
}

beforeEach(() => {
  window.localStorage.clear()
  vi.stubGlobal('WebSocket', MockWebSocket)
  // jsdom has no matchMedia; the theme hook reads it.
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
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe = vi.fn()
      disconnect = vi.fn()
    }
  )
})

afterEach(() => {
  vi.unstubAllGlobals()
  socket = null
})

it('starts live and moves only when the server confirms', () => {
  openWith(settings(true))
  expect(liveSwitch()).toHaveAccessibleName('Live')
  // A plain button: the name carries the state, so no pressed state contradicts it.
  expect(liveSwitch()).not.toHaveAttribute('aria-pressed')
  expect(ring()).not.toHaveClass('is-off-air')

  act(() => {
    liveSwitch().click()
  })
  expect(sentMessages().at(-1)).toEqual({ action: 'set_live', live: false })
  // Not optimistic: until the server agrees, the console still says live.
  expect(liveSwitch()).toHaveAccessibleName('Live')

  push(socket, { type: 'display_settings_confirmed', data: settings(false) })
  expect(liveSwitch()).toHaveAccessibleName('Off air')
  expect(liveSwitch()).not.toHaveAttribute('aria-pressed')
  // The title says what pressing does, in both states.
  expect(liveSwitch()).toHaveAccessibleDescription('Show the display to the audience again')
  expect(liveSwitch()).toHaveTextContent(/^Off air$/)
  // The switch says it; the preview is dimmed; no badge or tag repeats it.
  expect(ring()).toHaveClass('is-off-air')
  expect(screen.queryByText('Off air', { selector: '.status-badge, .status-badge *' })).toBeNull()
  expect(screen.getAllByText('Off air').every(el => el.closest('button'))).toBe(true)
  // Screen readers still hear what the audience sees.
  expect(screen.getByText('The audience sees a black screen')).toHaveClass('sr-only')
  expect(screen.getByRole('status')).toHaveTextContent('Display off air')
  // The preview keeps the passage: off air is where the operator checks it.
  expect(screen.getByText(passage.text)).toBeInTheDocument()

  act(() => {
    liveSwitch().click()
  })
  expect(sentMessages().at(-1)).toEqual({ action: 'set_live', live: true })
  push(socket, { type: 'display_settings_confirmed', data: settings(true) })
  expect(liveSwitch()).toHaveAccessibleName('Live')
  expect(liveSwitch()).toHaveAccessibleDescription(
    'Black out the audience display; this preview keeps working'
  )
  expect(ring()).not.toHaveClass('is-off-air')
  expect(screen.queryByText('The audience sees a black screen')).not.toBeInTheDocument()
})

it('holding Enter on Live sends only one change even after confirmation', () => {
  openWith(settings(true))
  const toggle = liveSwitch()
  act(() => toggle.focus())
  const messagesBeforeEnter = sentMessages()

  // jsdom does not synthesize the click for an uncanceled Enter keydown.
  if (fireEvent.keyDown(toggle, { key: 'Enter' })) fireEvent.click(toggle)
  const expected = [...messagesBeforeEnter, { action: 'set_live', live: false }]
  expect(sentMessages()).toEqual(expected)

  push(socket, { type: 'display_settings_confirmed', data: settings(false) })
  expect(toggle).toHaveFocus()
  if (fireEvent.keyDown(toggle, { key: 'Enter', repeat: true })) fireEvent.click(toggle)
  expect(sentMessages()).toEqual(expected)
})

it('a double-click on Live switches once, even when the server confirms between the clicks', () => {
  openWith(settings(true))
  const toggle = liveSwitch()
  const before = sentMessages()

  fireEvent.click(toggle, { detail: 1 })
  const expected = [...before, { action: 'set_live', live: false }]
  expect(sentMessages()).toEqual(expected)

  push(socket, { type: 'display_settings_confirmed', data: settings(false) })
  fireEvent.click(toggle, { detail: 2 })
  expect(sentMessages()).toEqual(expected)
})

it('a rejected switch stays where the server left it and says why', () => {
  openWith(settings(true))
  act(() => {
    liveSwitch().click()
  })
  push(socket, {
    type: 'error',
    data: { message: 'Invalid live; expected true or false', action: 'set_live' },
  })
  expect(liveSwitch()).toHaveAccessibleName('Live')
  expect(screen.getByRole('alert')).toHaveTextContent('Could not switch the display on or off air')
})

it('is read-only while disconnected', () => {
  openWith(settings(true))
  act(() => {
    socket?.onclose?.({ code: 1006 } as CloseEvent)
  })
  expect(liveSwitch()).toBeDisabled()
})

it('shows off air from the snapshot, as a console that connects mid-blackout must', () => {
  openWith(settings(false))
  expect(liveSwitch()).not.toHaveAttribute('aria-pressed')
  expect(liveSwitch()).toHaveAccessibleName('Off air')
  expect(liveSwitch()).not.toHaveClass('is-live')
  expect(ring()).toHaveClass('is-off-air')
})

it('marks Live with the on-air dot, filled by CSS only while live', () => {
  openWith(settings(true))

  const dot = liveSwitch().querySelector('.live-dot')
  expect(dot).toHaveAttribute('aria-hidden', 'true')
  expect(liveSwitch()).toHaveClass('btn-ghost', 'is-live')
  expect(liveSwitch()).toHaveAccessibleName('Live')
  expect(liveSwitch()).toHaveTextContent(/^Live$/)
})

it('treats a snapshot without the field, from an older server, as live', () => {
  openWith(settings())
  expect(liveSwitch()).toHaveAccessibleName('Live')
})
