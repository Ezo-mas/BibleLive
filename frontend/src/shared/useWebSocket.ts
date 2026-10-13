import { useCallback, useEffect, useRef, useState } from 'react'

export type WsStatus = 'connecting' | 'open' | 'closed'

interface UseWebSocketOptions {
  maxReconnectAttempts?: number
  onClose?: (event: CloseEvent) => void
  /**
   * Sent as soon as each connection opens, before anything else (the operator
   * console's token goes here rather than in the URL, where it would be
   * logged). A different value opens a new connection.
   */
  firstMessage?: unknown
}

interface UseWebSocketResult {
  status: WsStatus
  send: (message: unknown) => void
  /**
   * Replace the current socket with a fresh connection immediately.
   *
   * The automatic retries are finite, so a page that ran out of attempts (or
   * one whose retry is still waiting out its backoff) needs a way for the user
   * to ask again without reloading. This is a plain client-side reconnect: the
   * same path, the same messages, nothing new on the wire.
   */
  reconnect: () => void
}

/**
 * Open a WebSocket to `path` and keep it alive with linear backoff.
 *
 * `onMessage` receives each parsed JSON message. The handler may change
 * between renders without reconnecting the socket. Reconnection retries up
 * to `maxReconnectAttempts` times, waiting 3s * attempt between tries.
 *
 * `onClose` is called with the CloseEvent when the connection closes, useful
 * for handling authentication failures (code 1008).
 */
export function useWebSocket<T>(
  path: string,
  onMessage: (message: T) => void,
  options: UseWebSocketOptions = {}
): UseWebSocketResult {
  const { maxReconnectAttempts = 10, onClose, firstMessage } = options
  // Serialized once: it is what goes on the wire, and comparing the string lets
  // a caller pass a fresh object every render without reconnecting.
  const firstMessageText = firstMessage === undefined ? null : JSON.stringify(firstMessage)

  const [status, setStatus] = useState<WsStatus>('connecting')
  const wsRef = useRef<WebSocket | null>(null)
  const connectRef = useRef<(() => void) | null>(null)
  const handlerRef = useRef(onMessage)
  const closeHandlerRef = useRef(onClose)
  const attemptsRef = useRef(0)
  const reconnectTimerRef = useRef<number | undefined>(undefined)
  const unmountedRef = useRef(false)

  // Always invoke the latest handlers without reconnecting.
  handlerRef.current = onMessage
  closeHandlerRef.current = onClose

  useEffect(() => {
    unmountedRef.current = false

    const connect = () => {
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
      const ws = new WebSocket(`${protocol}//${window.location.host}${path}`)
      wsRef.current = ws

      ws.onopen = () => {
        // A socket that was replaced (manual reconnect) or unmounted must not
        // report its own state any more.
        if (ws !== wsRef.current) return
        if (firstMessageText !== null) ws.send(firstMessageText)
        attemptsRef.current = 0
        setStatus('open')
      }

      ws.onmessage = event => {
        if (ws !== wsRef.current) return
        try {
          handlerRef.current(JSON.parse(event.data) as T)
        } catch (e) {
          console.error('Error parsing WebSocket message:', e)
        }
      }

      ws.onclose = event => {
        // Ignore a superseded socket's late close event: it must neither flip
        // the status back to closed nor schedule another retry.
        if (ws !== wsRef.current) return

        setStatus('closed')

        // Call custom close handler if provided
        if (closeHandlerRef.current) {
          closeHandlerRef.current(event)
        }

        if (unmountedRef.current) return

        // Don't reconnect on auth failures (code 1008)
        if (event.code === 1008) return

        if (attemptsRef.current < maxReconnectAttempts) {
          attemptsRef.current += 1
          reconnectTimerRef.current = window.setTimeout(connect, 3000 * attemptsRef.current)
        }
      }

      ws.onerror = e => {
        console.error('WebSocket error:', e)
      }
    }

    connectRef.current = connect
    connect()

    return () => {
      unmountedRef.current = true
      connectRef.current = null
      window.clearTimeout(reconnectTimerRef.current)
      const ws = wsRef.current
      wsRef.current = null
      ws?.close()
    }
  }, [path, maxReconnectAttempts, firstMessageText])

  const reconnect = useCallback(() => {
    if (unmountedRef.current || !connectRef.current) return

    // Any queued retry is replaced by this one, and the attempt budget starts
    // over so a manual retry gets the full backoff schedule again.
    window.clearTimeout(reconnectTimerRef.current)
    attemptsRef.current = 0

    // Detach the old socket first: its close event then fails the guard above
    // instead of scheduling yet another connection.
    const previous = wsRef.current
    wsRef.current = null
    previous?.close()

    setStatus('connecting')
    connectRef.current()
  }, [])

  const send = useCallback((message: unknown) => {
    const ws = wsRef.current
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(message))
    }
  }, [])

  return { status, send, reconnect }
}
