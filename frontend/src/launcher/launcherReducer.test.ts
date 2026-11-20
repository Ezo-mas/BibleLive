import { describe, expect, it } from 'vitest'
import type { LogLine, PollResult } from './bridge'
import { createDemoBridge } from './demoBridge'
import { initialData, launcherReducer, LOG_LIMIT } from './launcherReducer'

describe('launcherReducer', () => {
  it('merges polled state and levels, and replaces only refreshed device options', async () => {
    const initial = await createDemoBridge().init()
    const data = initialData(initial)
    const result: PollResult = {
      state: { ...initial.state, audio: 'running', session: 'live' },
      levels: [0.15, 0.8],
      meter: 'on',
      meter_error: null,
      log: [],
      options: { microphone: [], projector: [{ value: 'new', label: 'New monitor' }] },
    }
    const next = launcherReducer(data, { type: 'poll', now: 100, result })
    expect(next.state).toEqual(result.state)
    expect(next.options).toEqual({ ...initial.options, ...result.options })
    expect(next.levels.map(sample => sample.level)).toEqual([0.15, 0.8])

    const restart = launcherReducer(next, {
      type: 'poll',
      now: 150,
      result: {
        ...result,
        state: { ...result.state, audio: 'starting' },
        levels: [0.4],
        options: null,
      },
    })
    expect(restart.options).toBe(next.options)
    expect(restart.levels.map(sample => sample.level)).toEqual([0.15, 0.8, 0.4])
    expect(restart.audioWasRunning).toBe(true)

    const stopped = launcherReducer(restart, {
      type: 'poll',
      now: 200,
      result: { ...result, state: initial.state, meter: 'off', levels: [], options: null },
    })
    expect(stopped.levels).toEqual([])
    expect(stopped.audioWasRunning).toBe(false)
    expect(stopped.sessionStopped).toBe(true)
  })

  it('appends log lines and caps history at the latest 2000 entries', async () => {
    const demo = createDemoBridge()
    const data = initialData(await demo.init())
    const poll = await demo.poll()
    const lines: LogLine[] = Array.from({ length: LOG_LIMIT }, (_, index) => ({
      time: '10:00:00',
      source: 'server',
      level: 'INFO',
      text: String(index),
    }))
    const full = launcherReducer(data, { type: 'poll', now: 50, result: { ...poll, log: lines } })
    const appended = launcherReducer(full, {
      type: 'poll',
      now: 100,
      result: { ...poll, log: poll.log },
    })
    expect(appended.log).toHaveLength(LOG_LIMIT)
    expect(appended.log[0].text).toBe('1')
    expect(appended.log[LOG_LIMIT - 1]).toBe(poll.log[0])
    expect(appended.logCount).toBe(LOG_LIMIT + 1)
  })
})
