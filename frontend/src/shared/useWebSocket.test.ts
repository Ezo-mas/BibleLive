import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, renderHook } from '@testing-library/react'
import { useWebSocket } from './useWebSocket'

interface MockSocket {
  onopen: (() => void) | null
  onmessage: ((event: { data: string }) => void) | null
  onclose: ((event: { code: number }) => void) | null
  onerror: ((event: unknown) => void) | null
  readyState: number
  send: ReturnType<typeof vi.fn>
  close: ReturnType<typeof vi.fn>
}

const created: MockSocket[] = []

const MockWebSocket = Object.assign(
  function MockWebSocketStub() {
    const instance: MockSocket = {
      onopen: null,
      onmessage: null,
      onclose: null,
      onerror: null,
      readyState: 1,
      send: vi.fn(),
      close: vi.fn(),
    }
    created.push(instance)
    return instance
  },
  { OPEN: 1, CLOSING: 2, CLOSED: 3 }
)

beforeEach(() => {
  vi.useFakeTimers()
  created.length = 0
  vi.stubGlobal('WebSocket', MockWebSocket)
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

/** Drop the newest socket and return how long the hook waited to replace it. */
function dropAndMeasureRetry(): number {
  const before = created.length
  act(() => {
    created[created.length - 1].onclose?.({ code: 1006 })
  })
  let waited = 0
  while (created.length === before) {
    act(() => {
      vi.advanceTimersByTime(1000)
    })
    waited += 1000
    if (waited > 120_000) throw new Error('no reconnect within two minutes')
  }
  return waited
}

describe('useWebSocket reconnect backoff', () => {
  it('grows by 3 s per attempt but never waits more than 30 s', () => {
    // The display page retries forever; uncapped, the twentieth retry would
    // wait a minute, leaving the projector dark long after the server is back.
    renderHook(() => useWebSocket('/ws/display', () => {}, { maxReconnectAttempts: Infinity }))

    const waits = Array.from({ length: 14 }, () => dropAndMeasureRetry())

    expect(waits.slice(0, 3)).toEqual([3000, 6000, 9000])
    expect(Math.max(...waits)).toBe(30_000)
    expect(waits.slice(-4)).toEqual([30_000, 30_000, 30_000, 30_000])
  })

  it('starts over at 3 s once a connection succeeds', () => {
    renderHook(() => useWebSocket('/ws/display', () => {}, { maxReconnectAttempts: Infinity }))
    for (let i = 0; i < 12; i += 1) dropAndMeasureRetry()

    act(() => {
      created[created.length - 1].onopen?.()
    })

    expect(dropAndMeasureRetry()).toBe(3000)
  })
})
