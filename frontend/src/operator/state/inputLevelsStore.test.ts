import { afterEach, expect, it, vi } from 'vitest'
import { createInputLevelsStore } from './inputLevelsStore'

afterEach(() => {
  vi.restoreAllMocks()
  vi.useRealTimers()
})

it('stays within one window of arrivals even when every bar timer is late', () => {
  vi.useFakeTimers()
  const timers: Window = window
  const setTimeout = timers.setTimeout.bind(timers)
  let barTimers = 0
  let fired = 0
  vi.spyOn(timers, 'setTimeout').mockImplementation((handler, delay) => {
    if (delay !== 200 || typeof handler !== 'function') return setTimeout(handler, delay)
    return setTimeout(
      () => {
        fired++
        handler()
      },
      delay + 1 + (barTimers++ % 4)
    )
  })

  const meter = createInputLevelsStore()
  const history = [0, 0, 0, 0, 0]
  for (let batch = 0; batch < 360; batch++) {
    // Most arrivals carry one window, with occasional catch-up bursts.
    const first = (2 * batch + 1) / 1000
    const peaks = batch % 3 === 0 ? [first, (2 * batch + 2) / 1000] : [first]
    history.push(...peaks)
    meter.receive(peaks.flatMap(peak => [0, peak, 0, 0]))
    const waiting = peaks.length - 1
    const end = history.length - waiting
    expect(meter.getSnapshot()?.bars).toEqual(history.slice(end - 5, end))
    expect(vi.getTimerCount()).toBe(1 + waiting) // Inactivity timer and at most one bar.
    vi.advanceTimersByTime(batch % 6 === 0 ? 400 : 200)
  }

  meter.receive([0.8, 0, 0, 0, 0.9, 0, 0, 0])
  expect(meter.getSnapshot()?.bars.at(-1)).toBe(0.8)
  vi.advanceTimersByTime(204)
  expect(meter.getSnapshot()?.bars.at(-1)).toBe(0.9)
  expect(fired).toBeGreaterThan(50)
  expect(barTimers - fired).toBeGreaterThan(50)
  meter.reset()
})

it('paces 500 ms capture bursts one real window at a time', () => {
  vi.useFakeTimers()
  const now = Date.now()
  const meter = createInputLevelsStore()
  const displayed: [number, number][] = []
  let last: number | undefined
  meter.subscribe(() => {
    const newest = meter.getSnapshot()?.bars.at(-1)
    if (newest !== undefined && newest !== last) displayed.push([Date.now() - now, newest])
    last = newest
  })

  for (let batch = 0; batch < 4; batch++) {
    meter.receive(Array.from({ length: 10 }, (_, index) => (batch * 10 + index + 1) / 100))
    expect(vi.getTimerCount()).toBe(2) // One playback timer plus inactivity.
    vi.advanceTimersByTime(500)
    expect(vi.getTimerCount()).toBe(1)
  }

  expect(displayed).toEqual([
    [0, 0.04],
    [200, 0.08],
    [500, 0.12],
    [700, 0.16],
    [900, 0.2],
    [1000, 0.24],
    [1200, 0.28],
    [1500, 0.32],
    [1700, 0.36],
    [1900, 0.4],
  ])
  expect(meter.getSnapshot()?.bars).toEqual([0.24, 0.28, 0.32, 0.36, 0.4])
  meter.reset()
})
