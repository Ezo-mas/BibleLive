import { act, fireEvent, render, screen } from '@testing-library/react'
import { createRef } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Console } from './Console'
import type { LogLine } from './bridge'
import { createDemoBridge } from './demoBridge'
import { LauncherI18nProvider } from './LauncherI18nProvider'
import { initialData } from './launcherReducer'

const lines: LogLine[] = [
  { time: '12:00:01', source: 'launcher', level: 'INFO', text: 'Starting' },
  { time: '12:00:02', source: 'server', level: 'WARNING', text: 'Slow start' },
  { time: '12:00:03', source: 'audio', level: 'DEBUG', text: 'Opened http://x/operator#token=abc' },
]

async function setup(log: LogLine[] = lines) {
  const bridge = createDemoBridge()
  const { state, options } = await bridge.init()
  const data = {
    ...initialData({
      platform: 'win32',
      language: 'en',
      view: 'advanced',
      form: {},
      state,
      options,
    } as never),
    log,
    logCount: log.length,
  }
  const logRef = createRef<HTMLDivElement>()
  const view = (next = data) => (
    <LauncherI18nProvider language="en">
      <Console
        data={next}
        logRef={logRef}
        visible
        initialFilters={{ source: 'all', level: 'DEBUG' }}
        onFilters={() => {}}
        bridge={bridge}
        onLogs={() => {}}
        onDiagnostics={() => {}}
      />
    </LauncherI18nProvider>
  )
  const result = render(view())
  return { bridge, data, logRef, view, ...result }
}

function scroll(element: HTMLElement, top: number) {
  Object.defineProperty(element, 'scrollHeight', { configurable: true, value: 1000 })
  Object.defineProperty(element, 'clientHeight', { configurable: true, value: 200 })
  element.scrollTop = top
  fireEvent.scroll(element)
}

afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
})

describe('console copy logs', () => {
  it('copies the visible lines as plain text, honouring filters and redacting tokens', async () => {
    const { bridge } = await setup()
    const write = vi.fn().mockResolvedValue(undefined)
    Object.assign(navigator, { clipboard: { writeText: write } })
    const button = screen.getByRole('button', { name: 'Copy logs' })
    fireEvent.click(button)
    await screen.findByText('Copied')
    expect(write).toHaveBeenCalledWith(
      [
        '12:00:01 INFO Launcher Starting',
        '12:00:02 WARNING Server Slow start',
        '12:00:03 DEBUG Audio Opened http://x/operator#token=[REDACTED]',
      ].join('\n')
    )

    fireEvent.change(screen.getByLabelText('Minimum level'), { target: { value: 'WARNING' } })
    fireEvent.click(screen.getByRole('button', { name: 'Server' }))
    fireEvent.click(button)
    await screen.findByText('Copied')
    expect(write).toHaveBeenLastCalledWith('12:00:02 WARNING Server Slow start')
    expect(bridge.copy_text).toBeDefined()
  })

  it('falls back to the bridge, reports failure, and returns to idle', async () => {
    vi.useFakeTimers()
    const { bridge } = await setup()
    Object.assign(navigator, { clipboard: undefined })
    const copy = vi.spyOn(bridge, 'copy_text').mockRejectedValue(new Error('no'))
    fireEvent.click(screen.getByRole('button', { name: 'Copy logs' }))
    await act(async () => {})
    expect(copy).toHaveBeenCalledTimes(1)
    expect(screen.getByText('Copy failed', { selector: '.bl-copy-label > span' })).toBeVisible()
    await act(async () => {
      vi.advanceTimersByTime(2100)
    })
    expect(screen.getByText('Copy logs', { selector: '.bl-copy-label > span' })).toBeVisible()
  })
})

describe('console paused indicator', () => {
  it('appears on scroll up, counts new lines, jumps to latest and hides at the bottom', async () => {
    const { logRef, data, view, rerender } = await setup()
    const log = logRef.current!
    expect(screen.queryByRole('button', { name: /Paused/ })).not.toBeInTheDocument()

    scroll(log, 100)
    const pill = screen.getByRole('button', { name: 'Paused — jump to latest' })
    const more = [...lines, { ...lines[0], time: '12:00:04', text: 'Later' }]
    rerender(view({ ...data, log: more, logCount: more.length }))
    expect(screen.getByRole('button', { name: 'Paused — jump to latest (1 new)' })).toBe(pill)

    fireEvent.click(pill)
    expect(log.scrollTop).toBe(1000)
    expect(screen.queryByRole('button', { name: /Paused/ })).not.toBeInTheDocument()

    scroll(log, 100)
    expect(screen.getByRole('button', { name: /Paused/ })).toBeVisible()
    scroll(log, 800)
    expect(screen.queryByRole('button', { name: /Paused/ })).not.toBeInTheDocument()
  })
})
