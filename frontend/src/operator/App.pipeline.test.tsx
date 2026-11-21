import { readFileSync } from 'node:fs'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, render, screen } from '@testing-library/react'
import App from './App'
import { PIPELINE_POLL_MS } from './hooks/usePipelineStatus'
import type { PipelineStatus } from '../shared/types'
import { createMockSocket, push } from '../test/operatorHarness'
import type { MockSocket } from '../test/operatorHarness'

const created: MockSocket[] = []

const MockWebSocket = Object.assign(
  function MockWebSocketStub() {
    const instance = createMockSocket()
    created.push(instance)
    return instance
  },
  { OPEN: 1, CLOSING: 2, CLOSED: 3 }
)

function latest() {
  return created[created.length - 1]
}

function sendLevels(...levels: number[]) {
  push(latest(), { type: 'input_levels', data: { levels } })
}

function bars() {
  return [...document.querySelectorAll<HTMLElement>('.operator-input-meter-bar')]
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

    const stale = screen.getByTitle(/speech detection is not running/i)
    expect(stale).toHaveClass('operator-language-status--stale')
    expect(stale.querySelector('.sr-only')).toHaveTextContent(/speech detection is not running/i)
    sendLevels(1, 1, 1, 1)
    expect(stale.querySelectorAll('svg line')).toHaveLength(5)
    expect(bars()).toHaveLength(0)

    await act(async () => {
      await vi.advanceTimersByTimeAsync(PIPELINE_POLL_MS)
    })

    const live = screen.getByTitle('Speech: English')
    expect(live).not.toHaveClass('operator-language-status--stale')
    expect(live.querySelector('.sr-only')).toHaveTextContent('Speech: English')
  })

  it('scrolls real peaks in four-sample windows and clears them after a gap', async () => {
    await renderConnected()
    sendLevels(0.08, 0.5, 0.2)
    expect(bars()).toHaveLength(5)
    expect(bars().map(bar => bar.dataset.zone)).toEqual(Array(5).fill('quiet'))

    sendLevels(0.1, 0.6, 0.45, 0.75, 0.5, 0.9, 0.1, 0.5, 0.2)
    expect(bars()[4]).toHaveStyle({ transform: 'scaleY(0.5)' })
    act(() => vi.advanceTimersByTime(199))
    expect(bars()[4]).toHaveStyle({ transform: 'scaleY(0.5)' })
    act(() => vi.advanceTimersByTime(1))
    expect(bars()[4]).toHaveStyle({ transform: 'scaleY(0.75)' })
    act(() => vi.advanceTimersByTime(200))
    expect(bars().map(bar => bar.style.transform)).toEqual([
      'scaleY(0.18)',
      'scaleY(0.18)',
      'scaleY(0.5)',
      'scaleY(0.75)',
      'scaleY(0.9)',
    ])
    expect(bars().map(bar => bar.dataset.zone)).toEqual([
      'quiet',
      'quiet',
      'green',
      'yellow',
      'red',
    ])
    expect(bars()[0].parentElement).toHaveAttribute('aria-hidden', 'true')

    act(() => vi.advanceTimersByTime(1099))
    expect(bars()[4]).toHaveStyle({ transform: 'scaleY(0.9)' })
    act(() => vi.advanceTimersByTime(2))
    expect(bars().map(bar => bar.style.transform)).toEqual(Array(5).fill('scaleY(0.18)'))
  })

  it('updates the badge without rebinding the console keyboard shortcuts', async () => {
    await renderConnected()
    const addListener = vi.spyOn(window, 'addEventListener')

    sendLevels(0, 0, 0, 0, 0.75, 0, 0, 0)
    expect(screen.getByTitle('Speech: English · input -∞ (peak -15 dB)')).toBeInTheDocument()
    act(() => vi.advanceTimersByTime(200))
    expect(bars()[4]).toHaveStyle({ transform: 'scaleY(0.75)' })
    sendLevels(0, 0, 0, 0)
    expect(bars()[4]).toHaveStyle({ transform: 'scaleY(0.18)' })

    expect(addListener.mock.calls.filter(([event]) => event === 'keydown')).toHaveLength(0)
  })

  it('uses the launcher silence reading for both tooltip values at zero', async () => {
    await renderConnected()
    sendLevels(0, 0, 0, 0)
    expect(screen.getByTitle('Speech: English · input -∞ (peak -∞)')).toBeInTheDocument()
  })

  it('discards level history and partial windows across a socket reconnect', async () => {
    await renderConnected()
    const original = latest()
    sendLevels(0.75, 0, 0, 0, 0.9, 0, 0, 0, 1)
    expect(bars()[4]).toHaveStyle({ transform: 'scaleY(0.75)' })

    act(() => original.onclose?.({ code: 1006 }))
    expect(bars()).toHaveLength(0)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000)
      expect(latest()).not.toBe(original)
      latest().onopen?.()
    })
    push(original, { type: 'input_levels', data: { levels: [1, 1, 1, 1] } })
    expect(bars()).toHaveLength(0)

    sendLevels(0.5, 0, 0)
    expect(bars().map(bar => bar.style.transform)).toEqual(Array(5).fill('scaleY(0.18)'))
    sendLevels(0)
    expect(bars()[4]).toHaveStyle({ transform: 'scaleY(0.5)' })
  })

  it('throttles the tooltip and leaves the screen-reader label steady', async () => {
    await renderConnected()
    sendLevels(0.1, 0.75, 0.4, 0.5)
    const badge = screen.getByTitle('Speech: English · input -30 dB (peak -15 dB)')
    sendLevels(0.8, 0, 0, 0.25)
    expect(badge).toHaveAttribute('title', 'Speech: English · input -30 dB (peak -15 dB)')
    act(() => vi.advanceTimersByTime(500))
    sendLevels(0, 0, 0, 0.5)
    act(() => vi.advanceTimersByTime(500))
    sendLevels(0, 0, 0, 0.25)
    expect(badge).toHaveAttribute('title', 'Speech: English · input -45 dB (peak -12 dB)')
    expect(badge.querySelector('.sr-only')).toHaveTextContent('Speech: English')
    expect(badge.querySelector('[aria-live]')).toBeNull()
  })

  it('marks the badge when pipeline health reports a silent microphone', async () => {
    statuses = [
      pipeline({
        status: 'degraded',
        issues: [{ code: 'mic_silent', params: { seconds: 35 }, text: 'Silent microphone' }],
      }),
    ]
    await renderConnected()
    const badge = screen.getByTitle('Speech: English')
    expect(badge).toHaveClass('operator-language-status--silent')
    sendLevels(0.5, 0, 0, 0)
    expect(bars()[4]).toHaveStyle({ transform: 'scaleY(0.5)' })
  })

  it('disables the meter transform transition in the reduced-motion stylesheet', () => {
    const headerStyles = readFileSync('src/operator/styles/header.css', 'utf8')
    expect(headerStyles).toMatch(
      /@media \(prefers-reduced-motion: reduce\)\s*\{\s*\.operator-input-meter-bar\s*\{\s*transition: none/
    )
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
