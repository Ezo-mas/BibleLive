import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, render, screen } from '@testing-library/react'
import App from './App'
import { PIPELINE_POLL_MS } from './hooks/usePipelineStatus'
import type { PipelineStatus } from '../shared/types'

/** The subset of WebSocket the operator app uses. */
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

function latest() {
  return created[created.length - 1]
}

/** Answers for /api/pipeline/status, served in order; the last one repeats. */
let statuses: PipelineStatus[] = []
const statusRequests: RequestInit[] = []

function pipeline(overrides: Partial<PipelineStatus>): PipelineStatus {
  return { status: 'ok', language: 'en', problems: [], seconds_since_report: 1, ...overrides }
}

function fakeFetch(url: string, init: RequestInit = {}) {
  if (url === '/api/pipeline/status') {
    statusRequests.push(init)
    const body = statuses.length > 1 ? statuses.shift() : statuses[0]
    return Promise.resolve(new Response(JSON.stringify(body), { status: 200 }))
  }
  if (url.startsWith('/api/language')) {
    return Promise.resolve(new Response(JSON.stringify({ language: 'en' }), { status: 200 }))
  }
  return Promise.resolve(new Response(JSON.stringify({ books: [] }), { status: 200 }))
}

/** Render the console, open its socket, and let the first poll answer. */
async function renderConnected() {
  render(<App />)
  await act(async () => {
    latest().onopen?.()
  })
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0)
  })
}

beforeEach(() => {
  vi.useFakeTimers()
  created.length = 0
  statuses = [pipeline({})]
  statusRequests.length = 0
  window.localStorage.clear()
  window.localStorage.setItem('biblelive_operator_token', 'secret')
  vi.stubGlobal('WebSocket', MockWebSocket)
  vi.stubGlobal('fetch', vi.fn(fakeFetch))
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
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('speech detection status', () => {
  it('shows no warning while the pipeline is healthy', async () => {
    await renderConnected()

    expect(statusRequests).toHaveLength(1)
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('lists what the pipeline reports as broken', async () => {
    statuses = [
      pipeline({
        status: 'degraded',
        problems: ['Audio capture stopped; the pipeline is trying to restart it'],
      }),
    ]
    await renderConnected()

    expect(screen.getByRole('alert')).toHaveTextContent(/speech detection has a problem/i)
    expect(
      screen.getByText('Audio capture stopped; the pipeline is trying to restart it')
    ).toBeInTheDocument()
  })

  it('says detection has stopped when the pipeline goes quiet', async () => {
    statuses = [pipeline({ status: 'offline', seconds_since_report: 20 })]
    await renderConnected()

    expect(screen.getByRole('alert')).toHaveTextContent(
      'Speech detection has stopped. The audio pipeline is not reporting (last heard 20 s ago), so no new references will arrive.'
    )
  })

  it('stays quiet when this server has never heard from a pipeline', async () => {
    statuses = [pipeline({ status: 'unknown', language: null, seconds_since_report: null })]
    await renderConnected()

    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('asks only while connected, with the operator token', async () => {
    render(<App />)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(PIPELINE_POLL_MS * 2)
    })
    expect(statusRequests).toHaveLength(0)

    await act(async () => {
      latest().onopen?.()
    })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })
    expect(statusRequests).toHaveLength(1)
    expect(statusRequests[0].headers).toEqual({ Authorization: 'Bearer secret' })
  })

  it('clears the warning once the pipeline recovers', async () => {
    statuses = [pipeline({ status: 'offline', seconds_since_report: 30 }), pipeline({})]
    await renderConnected()
    expect(screen.getByRole('alert')).toHaveTextContent(/speech detection has stopped/i)

    await act(async () => {
      await vi.advanceTimersByTimeAsync(PIPELINE_POLL_MS)
    })

    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('dims the spoken language while the pipeline is offline, and restores it', async () => {
    statuses = [pipeline({ status: 'offline', seconds_since_report: 30 }), pipeline({})]
    await renderConnected()

    const stale = screen.getByRole('button', { name: /^Spoken language/ })
    expect(stale).toHaveClass('operator-language-chip--stale')
    expect(stale).toHaveAccessibleName(/speech detection is not running/)

    await act(async () => {
      await vi.advanceTimersByTimeAsync(PIPELINE_POLL_MS)
    })

    const live = screen.getByRole('button', { name: /^Spoken language/ })
    expect(live).not.toHaveClass('operator-language-chip--stale')
    expect(live).toHaveAccessibleName('Spoken language: English. Change in settings')
  })

  it('drops a pipeline warning when the server connection drops', async () => {
    statuses = [pipeline({ status: 'offline', seconds_since_report: 30 })]
    await renderConnected()

    await act(async () => {
      latest().onclose?.({ code: 1006 })
    })

    // The connection banner says what is known; a pipeline guess would not.
    expect(screen.queryByText(/speech detection has stopped/i)).not.toBeInTheDocument()
    expect(screen.getByText(/not connected to the server/i)).toBeInTheDocument()
  })
})
