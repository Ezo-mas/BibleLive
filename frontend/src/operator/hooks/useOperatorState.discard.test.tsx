import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { BibleReference, OperatorMessage } from '../../shared/types'
import { useOperatorState } from './useOperatorState'

const socket = vi.hoisted(() => ({
  handler: (_message: OperatorMessage) => {},
  status: 'open',
  onClose: (_event: CloseEvent) => {},
  send: vi.fn(),
  reconnect: vi.fn(),
}))

vi.mock('../../shared/useWebSocket', () => ({
  useWebSocket: (
    _url: string,
    handler: (message: OperatorMessage) => void,
    options: { onClose: (event: CloseEvent) => void }
  ) => {
    socket.handler = handler
    socket.onClose = options.onClose
    return { status: socket.status, send: socket.send, reconnect: socket.reconnect }
  },
}))

beforeEach(() => {
  vi.useFakeTimers()
  window.localStorage.clear()
  socket.send.mockClear()
  socket.reconnect.mockClear()
  socket.status = 'open'
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.useRealTimers()
})

describe('discardPending', () => {
  it('sends the action and waits for pending_update to remove the highlighted row', () => {
    const ref: BibleReference = {
      id: 'pending-1',
      book: 'John',
      chapter: 3,
      verse_start: 16,
      verse_end: null,
      confidence: 'high',
      inferred: false,
      text: 'Verse text',
      verses: [{ verse: 16, text: 'Verse text' }],
      created_at: '2026-01-01T00:00:00Z',
    }
    const { result } = renderHook(() => useOperatorState())
    act(() => socket.handler({ type: 'pending_update', data: { pending: [ref], count: 1 } }))
    act(() => result.current.cursor.highlight('pending', ref.id))
    act(() => result.current.discardPending(ref.id))
    expect(result.current.discardInFlight).toBe(true)
    expect(socket.send).toHaveBeenCalledExactlyOnceWith({
      action: 'discard_pending',
      reference_id: ref.id,
    })
    expect(result.current.pending).toEqual([ref])
    expect(result.current.cursor.pendingCursorId).toBe(ref.id)
    act(() => socket.handler({ type: 'pending_update', data: { pending: [], count: 0 } }))
    expect(result.current.pending).toEqual([])
    expect(result.current.pendingCount).toBe(0)
    expect(result.current.cursor.pendingCursorId).toBeNull()
    expect(result.current.currentDisplay).toBeNull()
    expect(result.current.discardInFlight).toBe(true)
    act(() => vi.advanceTimersByTime(500))
    expect(result.current.discardInFlight).toBe(false)
  })

  it('ignores repeated discards until the requested id leaves the queue', () => {
    const { result } = renderHook(() => useOperatorState())
    act(() => {
      result.current.discardPending('first')
      result.current.discardPending('second')
    })
    expect(socket.send).toHaveBeenCalledTimes(1)
    expect(result.current.discardInFlight).toBe(true)
    const pending = [{ id: 'first' } as BibleReference]
    act(() => socket.handler({ type: 'pending_update', data: { pending, count: 1 } }))
    expect(result.current.discardInFlight).toBe(true)
    act(() => result.current.discardPending('second'))
    expect(socket.send).toHaveBeenCalledTimes(1)
    act(() => socket.handler({ type: 'pending_update', data: { pending: [], count: 0 } }))
    expect(result.current.discardInFlight).toBe(true)
    act(() => vi.advanceTimersByTime(499))
    act(() => result.current.discardPending('second'))
    expect(socket.send).toHaveBeenCalledTimes(1)
    // Another queue update must not restart the cooldown.
    act(() => socket.handler({ type: 'pending_update', data: { pending: [], count: 0 } }))
    act(() => vi.advanceTimersByTime(1))
    expect(result.current.discardInFlight).toBe(false)
    act(() => result.current.discardPending('second'))
    expect(socket.send).toHaveBeenLastCalledWith({
      action: 'discard_pending',
      reference_id: 'second',
    })
    expect(socket.send).toHaveBeenCalledTimes(2)
  })

  it('surfaces a discard error through the existing notice path', () => {
    const errorLog = vi.spyOn(console, 'error').mockImplementation(() => {})
    try {
      const { result } = renderHook(() => useOperatorState())
      act(() => result.current.discardPending('first'))
      expect(result.current.discardInFlight).toBe(true)
      act(() =>
        socket.handler({
          type: 'error',
          data: {
            action: 'discard_pending',
            message: 'Reference not found or already processed',
          },
        })
      )
      expect(result.current.notice?.tone).toBe('error')
      expect(result.current.notice?.text).toContain('Could not remove the reference')
      expect(result.current.notice?.text).toContain('Reference not found or already processed')
      expect(result.current.discardInFlight).toBe(false)
      act(() => result.current.discardPending('second'))
      expect(socket.send).toHaveBeenLastCalledWith({
        action: 'discard_pending',
        reference_id: 'second',
      })
    } finally {
      errorLog.mockRestore()
    }
  })

  it('clears a cooldown immediately on error without affecting the next discard', () => {
    const errorLog = vi.spyOn(console, 'error').mockImplementation(() => {})
    try {
      const { result } = renderHook(() => useOperatorState())
      act(() => result.current.discardPending('first'))
      act(() => socket.handler({ type: 'pending_update', data: { pending: [], count: 0 } }))
      act(() =>
        socket.handler({
          type: 'error',
          data: { action: 'discard_pending', message: 'Reference not found or already processed' },
        })
      )
      expect(result.current.discardInFlight).toBe(false)
      act(() => result.current.discardPending('second'))
      act(() => vi.advanceTimersByTime(500))
      expect(result.current.discardInFlight).toBe(true)
      expect(socket.send).toHaveBeenCalledTimes(2)
    } finally {
      errorLog.mockRestore()
    }
  })

  it('clears the cooldown timer on unmount', () => {
    const { result, unmount } = renderHook(() => useOperatorState())
    act(() => result.current.discardPending('first'))
    const setTimeoutSpy = vi.spyOn(window, 'setTimeout')
    const clearTimeoutSpy = vi.spyOn(window, 'clearTimeout')
    act(() => socket.handler({ type: 'pending_update', data: { pending: [], count: 0 } }))
    const cooldownIndex = setTimeoutSpy.mock.calls.findIndex(([, delay]) => delay === 500)
    expect(cooldownIndex).toBeGreaterThanOrEqual(0)
    const timer = setTimeoutSpy.mock.results[cooldownIndex].value
    unmount()
    expect(clearTimeoutSpy).toHaveBeenCalledWith(timer)
  })

  it.each(['disconnect', 'reconnect'])('clears the cooldown on %s', action => {
    const { result } = renderHook(() => useOperatorState())
    act(() => result.current.discardPending('first'))
    act(() => socket.handler({ type: 'pending_update', data: { pending: [], count: 0 } }))
    expect(result.current.discardInFlight).toBe(true)
    act(() => {
      if (action === 'disconnect') socket.onClose({ code: 1006 } as CloseEvent)
      else result.current.reconnect()
    })
    expect(result.current.discardInFlight).toBe(false)
    act(() => result.current.discardPending('second'))
    act(() => vi.advanceTimersByTime(500))
    expect(result.current.discardInFlight).toBe(true)
    expect(socket.send).toHaveBeenCalledTimes(2)
  })

  it('clears an in-flight discard when the socket closes or reconnects', () => {
    const { result, rerender } = renderHook(() => useOperatorState())
    act(() => result.current.discardPending('first'))
    act(() => socket.onClose({ code: 1006 } as CloseEvent))
    expect(result.current.discardInFlight).toBe(false)
    act(() => result.current.discardPending('second'))
    socket.status = 'connecting'
    rerender()
    expect(result.current.discardInFlight).toBe(false)
    act(() => result.current.discardPending('third'))
    expect(socket.send).toHaveBeenCalledTimes(2)
    socket.status = 'open'
    rerender()
    act(() => result.current.discardPending('third'))
    expect(socket.send).toHaveBeenCalledTimes(3)
    act(() => result.current.reconnect())
    expect(socket.reconnect).toHaveBeenCalledTimes(1)
    expect(result.current.discardInFlight).toBe(false)
  })
})
