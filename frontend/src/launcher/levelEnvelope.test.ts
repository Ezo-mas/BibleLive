import { describe, expect, it } from 'vitest'
import {
  advanceEnvelope,
  playLevels,
  queueLevels,
  rippleDistance,
  rippleLevel,
  RIPPLE_STEP_MS,
} from './levelEnvelope'

describe('level envelope', () => {
  it('moves partway on the first frame and reaches 80% of the step in 60 ms', () => {
    const first = advanceEnvelope(0, 1, 16)
    expect(first).toBeGreaterThan(0)
    expect(first).toBeLessThan(0.5)
    expect(advanceEnvelope(first, 1, 44)).toBeCloseTo(0.8)
  })

  it('decays to a third after 800 ms regardless of frame spacing', () => {
    const first = advanceEnvelope(0.9, 0, 150)
    expect(advanceEnvelope(first, 0, 650)).toBeCloseTo(0.3)
    expect(advanceEnvelope(0.9, 0, 800)).toBeCloseTo(0.3)
  })

  it('keeps falling until it reaches silence without new samples', () => {
    const released = playLevels(0.9, [], 0, 800)
    expect(playLevels(released, [], 800, 816)).toBeLessThan(released)
    expect(playLevels(released, [], 800, 6000)).toBe(0)
  })
})

describe('level playback', () => {
  it('plays a batch in order at 50 ms intervals, including when a frame crosses a boundary', () => {
    const queue = queueLevels([], [0.2, 0.8], 100)
    expect(queueLevels(queue, [0.4], 140)).toEqual([
      { time: 100, level: 0.2 },
      { time: 150, level: 0.8 },
      { time: 200, level: 0.4 },
    ])
    const first = playLevels(0, queue, 90, 150)
    expect(first).toBeCloseTo(advanceEnvelope(0, 0.2, 50))
    expect(playLevels(0, queue, 90, 175)).toBeCloseTo(advanceEnvelope(first, 0.8, 25))
    expect(playLevels(first, queue.slice(0, 1), 150, 180)).toBeLessThan(first)
  })

  it('drops a backlog exceeding 300 ms and starts the newest block at receipt', () => {
    const queue = queueLevels([], [0.1, 0.2, 0.3], 100)
    expect(queueLevels(queue, [0.4, 0.5, 0.6, 0.7], 120)).toEqual([{ time: 120, level: 0.7 }])
  })
})

describe('waveform ripple', () => {
  const count = 42
  const history = [
    { time: 0, level: 0 },
    { time: 20, level: 1 },
    { time: 40, level: 0 },
  ]
  const barAt = (index: number, now: number) =>
    rippleLevel(history, now, rippleDistance(index, count))

  it('starts at both centre bars and reaches mirrored bars one step per bar', () => {
    expect(barAt(20, 20)).toBe(1)
    expect(barAt(21, 20)).toBe(1)
    expect(barAt(19, 20)).toBe(0)
    expect(barAt(20, 20 + RIPPLE_STEP_MS)).toBe(0)
    expect(barAt(19, 20 + RIPPLE_STEP_MS)).toBe(1)
    expect(barAt(22, 20 + RIPPLE_STEP_MS)).toBe(1)
    expect(barAt(0, 20 + 20 * RIPPLE_STEP_MS)).toBe(1)
    expect(barAt(41, 20 + 20 * RIPPLE_STEP_MS)).toBe(1)
  })

  it('interpolates between history samples as the impulse travels', () => {
    expect(barAt(19, 10 + RIPPLE_STEP_MS)).toBeCloseTo(0.5)
    expect(barAt(19, 30 + RIPPLE_STEP_MS)).toBeCloseTo(0.5)
  })

  it('returns zero for every bar once silence fills the history', () => {
    const bars = Array.from({ length: count }, (_, index) => barAt(index, 1100))
    expect(bars).toEqual(Array<number>(count).fill(0))
  })
})
