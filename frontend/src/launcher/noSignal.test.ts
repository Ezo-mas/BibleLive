import { describe, expect, it } from 'vitest'
import { appendLevels, noSignal } from './noSignal'

describe('noSignal', () => {
  const quiet = Array.from({ length: 101 }, (_, index) => ({ time: index * 50, level: 0.099 }))

  it('needs a full five seconds of quiet samples while the meter is active', () => {
    expect(noSignal('on', quiet.slice(0, -1), 4950)).toBe(false)
    expect(noSignal('on', quiet, 5000)).toBe(true)
    expect(noSignal('off', quiet, 5000)).toBe(false)
    expect(noSignal('error', quiet, 5000)).toBe(false)
    expect(noSignal('on', quiet, 5150)).toBe(false)
  })

  it('clears immediately at the threshold and returns after five seconds of quiet', () => {
    const heard = appendLevels(quiet, [0.1], 5050)
    expect(noSignal('on', heard, 5050)).toBe(false)
    const next = appendLevels(
      heard,
      Array.from({ length: 100 }, () => 0.02),
      10_050
    )
    expect(noSignal('on', next, 10_050)).toBe(true)
  })

  it('starts a fresh quiet window after a gap in samples', () => {
    const resumed = appendLevels(quiet, [0.02], 5500)
    expect(noSignal('on', resumed, 5500)).toBe(false)
  })
})
