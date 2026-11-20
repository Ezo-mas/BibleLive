import { expect, it } from 'vitest'
import { formatElapsed } from './elapsed'

it('formats completed seconds including hours, and clamps a future start to zero', () => {
  expect(formatElapsed(1000, 5_026_900)).toBe('01:23:45')
  expect(formatElapsed(1000, 999)).toBe('00:00:00')
  expect(formatElapsed(0, 360_000_000)).toBe('100:00:00')
})
