import { beforeEach, describe, expect, it } from 'vitest'
import type { BibleReference, VerseText } from './types'
import {
  DEFAULT_DISPLAY_THEME,
  DEFAULT_VERSE_LIMIT,
  DISPLAY_THEME_OPTIONS,
  DISPLAY_THEME_STORAGE_KEY,
  VERSE_LIMIT_OPTIONS,
  VERSE_LIMIT_STORAGE_KEY,
  coerceDisplayTheme,
  coerceVerseLimit,
  hiddenVerseCount,
  readStoredDisplayTheme,
  readStoredVerseLimit,
  selectVerses,
  writeStoredDisplayTheme,
  writeStoredVerseLimit,
} from './verseText'

function ref(overrides: Partial<BibleReference> = {}): BibleReference {
  return {
    id: 'test-id',
    book: 'Psalms',
    chapter: 119,
    verse_start: 1,
    verse_end: 8,
    confidence: 'high',
    inferred: false,
    text: '',
    verses: [],
    created_at: '2026-01-01T00:00:00Z',
    ...overrides,
  }
}

function verses(count: number): VerseText[] {
  return Array.from({ length: count }, (_, index) => ({
    verse: index + 1,
    text: `Verse ${index + 1}`,
  }))
}

describe('selectVerses', () => {
  it('returns nothing without a reference', () => {
    expect(selectVerses(null)).toEqual([])
  })

  it('defaults to 3 verses', () => {
    expect(DEFAULT_VERSE_LIMIT).toBe(3)
    expect(selectVerses(ref({ verses: verses(8) })).map((v) => v.verse)).toEqual([1, 2, 3])
  })

  it('shows 5 verses when requested', () => {
    const shown = selectVerses(ref({ verses: verses(8) }), 5)
    expect(shown.map((v) => v.verse)).toEqual([1, 2, 3, 4, 5])
  })

  it('shows every verse for "all"', () => {
    expect(selectVerses(ref({ verses: verses(8) }), 'all')).toHaveLength(8)
  })

  it('never returns more verses than exist', () => {
    expect(selectVerses(ref({ verses: verses(2) }), 5)).toHaveLength(2)
  })

  it('falls back to the joined text when verses are missing', () => {
    const fallback = selectVerses(ref({ text: 'In the beginning.' }), 3)
    expect(fallback).toEqual([{ verse: 1, text: 'In the beginning.' }])
  })

  it('returns nothing when there is no text at all', () => {
    expect(selectVerses(ref())).toEqual([])
  })

  it('prefers per-verse text over the joined text', () => {
    const shown = selectVerses(ref({ verses: verses(2), text: 'joined' }), 'all')
    expect(shown.map((v) => v.text)).toEqual(['Verse 1', 'Verse 2'])
  })
})

describe('hiddenVerseCount', () => {
  it('counts verses hidden by the limit', () => {
    expect(hiddenVerseCount(ref({ verses: verses(8) }), 3)).toBe(5)
  })

  it('reports zero when everything fits', () => {
    expect(hiddenVerseCount(ref({ verses: verses(2) }), 5)).toBe(0)
  })

  it('reports zero for "all"', () => {
    expect(hiddenVerseCount(ref({ verses: verses(8) }), 'all')).toBe(0)
  })

  it('is zero without a reference', () => {
    expect(hiddenVerseCount(null, 3)).toBe(0)
  })
})

describe('verse limit storage', () => {
  beforeEach(() => {
    window.localStorage.clear()
  })

  it('defaults to 3 when nothing is stored', () => {
    expect(readStoredVerseLimit(window.localStorage)).toBe(3)
  })

  it('round-trips a numeric limit', () => {
    writeStoredVerseLimit(5, window.localStorage)
    expect(readStoredVerseLimit(window.localStorage)).toBe(5)
  })

  it('round-trips "all"', () => {
    writeStoredVerseLimit('all', window.localStorage)
    expect(readStoredVerseLimit(window.localStorage)).toBe('all')
  })

  it('ignores unknown stored values', () => {
    window.localStorage.setItem(VERSE_LIMIT_STORAGE_KEY, '99')
    expect(readStoredVerseLimit(window.localStorage)).toBe(3)
  })

  it('ignores garbage stored values', () => {
    window.localStorage.setItem(VERSE_LIMIT_STORAGE_KEY, 'abc')
    expect(readStoredVerseLimit(window.localStorage)).toBe(3)
  })

  it('exposes the allowed options', () => {
    expect([...VERSE_LIMIT_OPTIONS]).toEqual([3, 5, 10])
  })
})

describe('coerceVerseLimit', () => {
  it('passes through every allowed option', () => {
    for (const option of VERSE_LIMIT_OPTIONS) {
      expect(coerceVerseLimit(option)).toBe(option)
    }
    expect(coerceVerseLimit('all')).toBe('all')
  })

  it('falls back to the default for values this build does not know', () => {
    // A server from a different version must not blank or widen the display.
    expect(coerceVerseLimit(7)).toBe(DEFAULT_VERSE_LIMIT)
    expect(coerceVerseLimit(0)).toBe(DEFAULT_VERSE_LIMIT)
    expect(coerceVerseLimit(-3)).toBe(DEFAULT_VERSE_LIMIT)
  })

  it('falls back to the default for wrong types', () => {
    expect(coerceVerseLimit('3')).toBe(DEFAULT_VERSE_LIMIT)
    expect(coerceVerseLimit('ALL')).toBe(DEFAULT_VERSE_LIMIT)
    expect(coerceVerseLimit(null)).toBe(DEFAULT_VERSE_LIMIT)
    expect(coerceVerseLimit(undefined)).toBe(DEFAULT_VERSE_LIMIT)
    expect(coerceVerseLimit({})).toBe(DEFAULT_VERSE_LIMIT)
  })
})

describe('display theme', () => {
  beforeEach(() => {
    window.localStorage.clear()
  })

  it('defaults to dark for a projector', () => {
    expect(DEFAULT_DISPLAY_THEME).toBe('dark')
    expect(readStoredDisplayTheme(window.localStorage)).toBe('dark')
  })

  it('exposes dark and light as the only options', () => {
    expect([...DISPLAY_THEME_OPTIONS]).toEqual(['dark', 'light'])
  })

  it('round-trips a stored theme', () => {
    writeStoredDisplayTheme('light', window.localStorage)
    expect(readStoredDisplayTheme(window.localStorage)).toBe('light')
    writeStoredDisplayTheme('dark', window.localStorage)
    expect(readStoredDisplayTheme(window.localStorage)).toBe('dark')
  })

  it('ignores an unknown stored theme', () => {
    window.localStorage.setItem(DISPLAY_THEME_STORAGE_KEY, 'sepia')
    expect(readStoredDisplayTheme(window.localStorage)).toBe('dark')
  })

  it('passes through both known themes', () => {
    expect(coerceDisplayTheme('dark')).toBe('dark')
    expect(coerceDisplayTheme('light')).toBe('light')
  })

  it('falls back to dark for values this build does not know', () => {
    // An unknown theme must not leave the projector on an unreadable background.
    expect(coerceDisplayTheme('DARK')).toBe('dark')
    expect(coerceDisplayTheme('system')).toBe('dark')
    expect(coerceDisplayTheme(null)).toBe('dark')
    expect(coerceDisplayTheme(undefined)).toBe('dark')
  })
})