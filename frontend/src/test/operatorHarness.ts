import { act } from '@testing-library/react'
import { vi } from 'vitest'
import type { DisplayMessage, OperatorMessage } from '../shared/types'

/**
 * The subset of WebSocket the operator and display apps use, plus the URL the
 * socket was opened with (App.auth checks the token handshake against it).
 */
export interface MockSocket {
  url: string
  onopen: (() => void) | null
  onmessage: ((event: { data: string }) => void) | null
  onclose: ((event: { code: number }) => void) | null
  onerror: ((event: unknown) => void) | null
  readyState: number
  send: ReturnType<typeof vi.fn>
  close: ReturnType<typeof vi.fn>
}

/**
 * A fresh open socket. The test file decides where to keep it: a `socket`
 * variable for one instance, or a list when a reconnect must be observable.
 */
export function createMockSocket(url = ''): MockSocket {
  return {
    url,
    onopen: null,
    onmessage: null,
    onclose: null,
    onerror: null,
    readyState: 1,
    send: vi.fn(),
    close: vi.fn(),
  }
}

/** Deliver a server message to the app that opened `socket`. */
export function push(socket: MockSocket | null, message: OperatorMessage | DisplayMessage) {
  act(() => {
    socket?.onmessage?.({ data: JSON.stringify(message) })
  })
}

/** The last JSON message `socket` sent, or null if it sent none. */
export function lastSent(socket: MockSocket | null) {
  const calls = socket?.send.mock.calls ?? []
  return calls.length ? JSON.parse(calls[calls.length - 1][0]) : null
}

/** A fetch Response carrying a JSON body. */
export function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as unknown as Response
}
