import { describe, expect, it } from 'vitest'
import {
  computeFitFontSize,
  fitFontSizeByMeasurement,
  MAX_FONT_SIZE,
  MIN_FONT_SIZE,
} from './fitText'

const box = { widthPx: 1600, heightPx: 900, lineHeight: 1.35 }

describe('computeFitFontSize', () => {
  it('uses the maximum size for a short verse on a large screen', () => {
    const size = computeFitFontSize({
      ...box,
      charCount: 120,
      lineCount: 1,
    })
    expect(size).toBe(MAX_FONT_SIZE)
  })

  it('shrinks as the text gets longer', () => {
    const short = computeFitFontSize({ ...box, charCount: 120, lineCount: 1 })
    const long = computeFitFontSize({ ...box, charCount: 1200, lineCount: 1 })
    expect(long).toBeLessThan(short)
  })

  it('shrinks as more verses are shown', () => {
    const one = computeFitFontSize({ ...box, charCount: 300, lineCount: 1 })
    const three = computeFitFontSize({ ...box, charCount: 300, lineCount: 3 })
    expect(three).toBeLessThan(one)
  })

  it('shrinks on a small viewport', () => {
    const large = computeFitFontSize({ ...box, charCount: 900, lineCount: 3 })
    const small = computeFitFontSize({
      ...box,
      charCount: 900,
      lineCount: 3,
      widthPx: 500,
      heightPx: 400,
    })
    expect(small).toBeLessThan(large)
  })

  it('never returns less than the minimum readable size', () => {
    const size = computeFitFontSize({
      ...box,
      charCount: 20000,
      lineCount: 10,
      widthPx: 200,
      heightPx: 100,
    })
    expect(size).toBeGreaterThanOrEqual(MIN_FONT_SIZE)
  })

  it('stays within the size bounds for every input shape', () => {
    const cases = [
      { charCount: 0, lineCount: 1 },
      { charCount: 1, lineCount: 1 },
      { charCount: 500, lineCount: 5 },
      { charCount: 100000, lineCount: 50 },
    ]
    for (const input of cases) {
      const size = computeFitFontSize({ ...box, ...input })
      expect(size).toBeGreaterThanOrEqual(MIN_FONT_SIZE)
      expect(size).toBeLessThanOrEqual(MAX_FONT_SIZE)
    }
  })

  it('handles zero-size boxes without dividing by zero', () => {
    const size = computeFitFontSize({
      charCount: 100,
      lineCount: 1,
      widthPx: 0,
      heightPx: 0,
      lineHeight: 1.35,
    })
    expect(Number.isFinite(size)).toBe(true)
    expect(size).toBeGreaterThanOrEqual(MIN_FONT_SIZE)
  })
})

/** Simulates text whose height grows linearly with the font size. */
function makeFitter(available: number, heightPerPx: number) {
  let current = MIN_FONT_SIZE
  const applied: number[] = []
  return {
    applied,
    run: (estimate: number) =>
      fitFontSizeByMeasurement(
        estimate,
        size => {
          current = size
          applied.push(size)
        },
        () => current * heightPerPx,
        available
      ),
  }
}

describe('fitFontSizeByMeasurement', () => {
  it('keeps the estimate when nothing is laid out', () => {
    const size = fitFontSizeByMeasurement(
      40,
      () => {},
      () => 0,
      0
    )
    expect(size).toBe(40)
  })

  it('shrinks until the text fits', () => {
    const { run } = makeFitter(200, 10)
    const size = run(60)
    expect(size).toBeLessThan(60)
    expect(size * 10).toBeLessThanOrEqual(200)
  })

  it('grows to reclaim unused space', () => {
    const { run } = makeFitter(600, 10)
    const size = run(20)
    expect(size).toBeGreaterThan(20)
    expect(size * 10).toBeLessThanOrEqual(600)
  })

  it('never grows past the point where the text would overflow', () => {
    const { run } = makeFitter(300, 12)
    const size = run(16)
    expect(size * 12).toBeLessThanOrEqual(300)
  })

  it('never returns below the minimum size', () => {
    const { run } = makeFitter(10, 100)
    expect(run(40)).toBe(MIN_FONT_SIZE)
  })

  it('never returns above the maximum size', () => {
    const { run } = makeFitter(100000, 1)
    expect(run(10)).toBe(MAX_FONT_SIZE)
  })

  it('leaves the element at the chosen size', () => {
    const { run, applied } = makeFitter(400, 8)
    const size = run(20)
    expect(applied[applied.length - 1]).toBe(size)
  })

  it('does not grow when the next size would overflow', () => {
    // Non-linear: 30 fits, 31 does not, even though the slack looks large.
    let current = 0
    const size = fitFontSizeByMeasurement(
      30,
      next => {
        current = next
      },
      () => (current <= 30 ? 200 : 260),
      240
    )
    expect(size).toBe(30)
  })
})
