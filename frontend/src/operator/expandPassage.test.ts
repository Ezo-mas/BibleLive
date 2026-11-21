import { describe, expect, it } from 'vitest'
import { expandPassage } from './expandPassage'

describe('expandPassage', () => {
  it.each([
    [10, null, 10, 11],
    [10, 11, 10, 12],
    [10, 12, 11, 13],
    [1, 5, 4, 6],
  ])('expands %i to %s into %i to %i', (start, end, nextStart, nextEnd) => {
    expect(expandPassage(start, end)).toEqual({ verseStart: nextStart, verseEnd: nextEnd })
  })
})
