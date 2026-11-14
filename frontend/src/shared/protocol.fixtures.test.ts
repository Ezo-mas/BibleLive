import { createElement } from 'react'
import { act, render, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import DisplayApp from '../display/App'
import StreamOverlay from '../display/StreamOverlay'
import { useOperatorState } from '../operator/hooks/useOperatorState'
import type { DisplayMessage, OperatorAction, OperatorMessage } from './types'

const socket = vi.hoisted(() => ({
  handlers: new Map<string, (message: never) => void>(),
  send: vi.fn(),
  reconnect: vi.fn(),
}))

// Capture the callbacks passed by the real pages, leaving their handlers intact.
vi.mock('./useWebSocket', () => ({
  useWebSocket: (url: string, handler: (message: never) => void) => {
    socket.handlers.set(url, handler)
    return { status: 'connecting', send: socket.send, reconnect: socket.reconnect }
  },
}))

const operator = import.meta.glob<OperatorMessage>(
  '../../../backend/tests/fixtures/protocol/operator/*.json',
  { eager: true, import: 'default' }
)
const display = import.meta.glob<DisplayMessage>(
  '../../../backend/tests/fixtures/protocol/display/*.json',
  { eager: true, import: 'default' }
)
const actions = import.meta.glob<OperatorAction>(
  '../../../backend/tests/fixtures/protocol/actions/*.json',
  { eager: true, import: 'default' }
)

beforeEach(() => {
  vi.useFakeTimers()
  window.localStorage.clear()
  document.documentElement.setAttribute('data-theme', 'dark')
  socket.handlers.clear()
  vi.spyOn(console, 'warn').mockImplementation(() => {})
  vi.spyOn(console, 'error').mockImplementation(() => {})
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
  vi.useRealTimers()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

function deliver(url: string, message: OperatorMessage | DisplayMessage) {
  const handler = socket.handlers.get(url)
  expect(handler).toBeDefined()
  act(() => handler!(message as never))
}

describe('checked-in protocol fixtures', () => {
  it('loads every operator action example', () => {
    expect(Object.keys(actions)).toHaveLength(9)
    for (const [path, message] of Object.entries(actions)) {
      expect(path.endsWith(`/${message.action}.json`)).toBe(true)
    }
  })

  it.each(Object.entries(operator))('handles operator fixture %s', (path, message) => {
    expect([
      'approval_confirmed',
      'auto_mode_confirmed',
      'bible_version_confirmed',
      'clear_confirmed',
      'connection_status',
      'display_confirmed',
      'display_settings_confirmed',
      'error',
      'follow_along',
      'follow_auto_confirmed',
      'pending_cleared',
      'pending_update',
      'state_snapshot',
    ]).toContain(message.type)
    expect(path.endsWith(`/${message.type}.json`)).toBe(true)
    const { result } = renderHook(() => useOperatorState())
    let dataRead = false
    const tracked = Object.defineProperty({ ...message }, 'data', {
      get: () => {
        dataRead = true
        return message.data
      },
    })
    deliver('/ws/operator', tracked)
    // The two empty confirmations have observable effects without reading data.
    // All other cases consume their payload, so a silent fall-through fails too.
    if (message.type === 'clear_confirmed' || message.type === 'pending_cleared') {
      expect(result.current.notice?.tone).toBe('info')
    } else {
      expect(dataRead).toBe(true)
    }
    expect(console.warn).not.toHaveBeenCalled()
    if (message.type === 'error') {
      expect(console.error).toHaveBeenCalledWith('Server error:', message.data.message)
      expect(result.current.notice?.tone).toBe('error')
    } else {
      expect(console.error).not.toHaveBeenCalled()
    }
  })

  it.each(
    Object.entries(display).flatMap(([path, message]) =>
      [
        ['projector', DisplayApp],
        ['overlay', StreamOverlay],
      ].map(([surface, component]) => ({ path, message, surface, component }))
    )
  )('handles $path on $surface', ({ path, message, surface, component }) => {
    expect(['reference', 'clear', 'display_settings']).toContain(message.type)
    expect(path.endsWith(`/${message.type}.json`)).toBe(true)
    const { container } = render(createElement(component as typeof DisplayApp))
    const reference = Object.values(display).find(example => example.type === 'reference')!
    if (reference.type !== 'reference') throw new Error('Missing reference fixture')
    // The captured reference has one verse. Extend it to distinguish the default
    // limit of three from the fixture's limit of five on both surfaces.
    const passage = {
      ...reference,
      data: {
        ...reference.data,
        verses: Array.from({ length: 6 }, (_, index) => ({
          verse: index + 1,
          text: `Fixture verse ${index + 1}`,
        })),
      },
    }
    if (message.type === 'clear') {
      deliver('/ws/display', reference)
      act(() => vi.advanceTimersByTime(400))
      expect(container.textContent).toContain(reference.data.verses[0].text)
    }
    if (message.type === 'display_settings') {
      deliver('/ws/display', passage)
      act(() => vi.advanceTimersByTime(400))
      expect(container.textContent).not.toContain('Fixture verse 5')
    }
    deliver('/ws/display', message)
    act(() => vi.advanceTimersByTime(400))
    switch (message.type) {
      case 'reference':
        expect(container.textContent).toContain(message.data.verses[0].text)
        break
      case 'clear':
        expect(container.textContent).not.toContain(reference.data.verses[0].text)
        break
      case 'display_settings':
        expect(container.textContent).toContain('Fixture verse 5')
        expect(container.textContent).not.toContain('Fixture verse 6')
        expect(document.documentElement.getAttribute('data-theme')).toBe(
          surface === 'projector' ? message.data.theme : 'dark'
        )
        break
    }
    expect(console.warn).not.toHaveBeenCalled()
    expect(console.error).not.toHaveBeenCalled()
  })
})
