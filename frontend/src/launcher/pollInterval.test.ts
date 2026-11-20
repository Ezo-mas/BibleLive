import { describe, expect, it } from 'vitest'
import type { Meter, State } from './bridge'
import { pollInterval } from './pollInterval'

describe('launcher poll interval', () => {
  const visible: Record<State['session'], Record<Meter, number>> = {
    idle: { off: 250, starting: 50, on: 50, error: 250 },
    starting: { off: 250, starting: 250, on: 250, error: 250 },
    live: { off: 250, starting: 250, on: 250, error: 250 },
    stopping: { off: 250, starting: 250, on: 250, error: 250 },
  }

  it.each(['idle', 'starting', 'live', 'stopping'] as const)(
    'uses the %s polling schedule',
    session => {
      for (const meter of ['off', 'starting', 'on', 'error'] as const) {
        expect(pollInterval(session, meter, false)).toBe(visible[session][meter])
        expect(pollInterval(session, meter, true)).toBe(1000)
      }
    }
  )
})
