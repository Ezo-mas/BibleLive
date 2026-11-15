import { act, renderHook } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { OperatorMessage } from '../../shared/types'
import { useOperatorState } from './useOperatorState'

const socket = vi.hoisted(() => ({
  handler: (_message: OperatorMessage) => {},
}))

vi.mock('../../shared/useWebSocket', () => ({
  useWebSocket: (_url: string, handler: (message: OperatorMessage) => void) => {
    socket.handler = handler
    return { status: 'open', send: vi.fn(), reconnect: vi.fn() }
  },
}))

beforeEach(() => {
  window.localStorage.clear()
  vi.spyOn(console, 'error').mockImplementation(() => {})
})

describe('error messages with an echoed action', () => {
  it.each([123, null, { action: 'approve' }, ['clear'], 'constructor', 'toString', 'no_such'])(
    'falls back to the generic prefix for %j',
    action => {
      const { result } = renderHook(() => useOperatorState())
      act(() => socket.handler({ type: 'error', data: { message: 'Unknown action', action } }))
      expect(result.current.notice?.tone).toBe('error')
      expect(result.current.notice?.text).toBe('Action failed: Unknown action')
    }
  )

  it('keeps the action-specific prefix for a known action', () => {
    const { result } = renderHook(() => useOperatorState())
    act(() => socket.handler({ type: 'error', data: { message: 'Nope', action: 'clear_pending' } }))
    expect(result.current.notice?.text).toMatch(/: Nope$/)
    expect(result.current.notice?.text).not.toBe('Action failed: Nope')
  })
})
