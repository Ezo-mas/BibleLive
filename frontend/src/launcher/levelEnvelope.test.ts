import { describe, expect, it } from 'vitest'
import { advanceEnvelope } from './levelEnvelope'

describe('level envelope', () => {
  it('attacks to a new peak on the next frame', () => {
    expect(advanceEnvelope(0.2, 0.9, 16, true)).toBe(0.9)
  })

  it('decays to a third after 350 ms regardless of frame spacing', () => {
    const first = advanceEnvelope(0.9, 0, 150, true)
    expect(advanceEnvelope(first, 0, 200, true)).toBeCloseTo(0.3)
    expect(advanceEnvelope(0.9, 0, 350, true)).toBeCloseTo(0.3)
  })

  it('keeps falling until it reaches silence without new samples', () => {
    const released = advanceEnvelope(0.9, 0, 350, true)
    expect(advanceEnvelope(released, 0, 16, true)).toBeLessThan(released)
    expect(advanceEnvelope(released, 0, 3000, true)).toBe(0)
  })

  it('clears the envelope when the meter is off, even with pending input', () => {
    expect(advanceEnvelope(0.9, 1, 16, false)).toBe(0)
  })
})
