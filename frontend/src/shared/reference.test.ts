import { describe, expect, it } from 'vitest'
import type { BibleReference } from './types'
import { formatReference } from './reference'

function ref(overrides: Partial<BibleReference>): BibleReference {
  return {
    id: 'test-id',
    book: 'John',
    chapter: 3,
    verse_start: 16,
    verse_end: 16,
    confidence: 'high',
    inferred: false,
    text: '',
    verses: [],
    created_at: '2026-01-01T00:00:00Z',
    ...overrides,
  }
}

describe('formatReference', () => {
  it('formats a single verse', () => {
    expect(formatReference(ref({}))).toBe('John 3:16')
  })

  it('formats a verse range', () => {
    expect(formatReference(ref({ verse_start: 16, verse_end: 18 }))).toBe('John 3:16-18')
  })

  it('omits duplicate verse_end', () => {
    expect(formatReference(ref({ verse_start: 16, verse_end: 16 }))).toBe('John 3:16')
  })

  it('omits verse numbers for chapter-only references', () => {
    expect(formatReference(ref({ verse_start: null, verse_end: null }))).toBe('John 3')
  })

  it('handles missing verse_end for single verse', () => {
    expect(formatReference(ref({ verse_end: null }))).toBe('John 3:16')
  })
})
