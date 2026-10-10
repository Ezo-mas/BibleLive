import { describe, expect, it } from 'vitest'
import { BOOK_RESULT_LIMIT, buildResults, matchBooks, parseScriptureQuery } from './scriptureQuery'
import type { ScriptureBook } from '../shared/types'

/** A slice of the canon, with the abbreviation and variant data the server sends. */
const GENESIS: ScriptureBook = {
  name: 'Genesis',
  chapters: 50,
  order: 1,
  abbrev: 'gn',
  variants: ['genesis'],
}
const PSALMS: ScriptureBook = {
  name: 'Psalms',
  chapters: 150,
  order: 19,
  abbrev: 'ps',
  variants: ['psalms', 'psalm'],
}
const SONG: ScriptureBook = {
  name: 'Song of Solomon',
  chapters: 8,
  order: 22,
  abbrev: 'so',
  variants: ['song of solomon', 'song of songs', 'canticles'],
}
const JOHN: ScriptureBook = {
  name: 'John',
  chapters: 21,
  order: 43,
  abbrev: 'jn',
  variants: ['john'],
}
const FIRST_JOHN: ScriptureBook = {
  name: '1 John',
  chapters: 5,
  order: 62,
  abbrev: '1jo',
  variants: ['john'],
}
const FIRST_CORINTHIANS: ScriptureBook = {
  name: '1 Corinthians',
  chapters: 16,
  order: 46,
  abbrev: '1co',
  variants: ['corinthians'],
}
const SECOND_TIMOTHY: ScriptureBook = {
  name: '2 Timothy',
  chapters: 4,
  order: 55,
  abbrev: '2tm',
  variants: ['timothy'],
}

const BOOKS = [GENESIS, PSALMS, SONG, JOHN, FIRST_JOHN, FIRST_CORINTHIANS, SECOND_TIMOTHY]

/** Book names matching, best first. */
function names(query: string, books: ScriptureBook[] = BOOKS): string[] {
  return matchBooks(query, books).map(match => match.book.name)
}

describe('matchBooks', () => {
  it('matches a book-name prefix and a substring', () => {
    expect(names('ge')).toEqual(['Genesis'])
    expect(names('gen')).toEqual(['Genesis'])
    expect(names('enes')).toEqual(['Genesis'])
    expect(names('ps')).toEqual(['Psalms'])
  })

  it('matches the canon abbreviations and name variants', () => {
    expect(names('gn')).toEqual(['Genesis'])
    expect(names('jn')).toEqual(['John'])
    expect(names('psalm')).toEqual(['Psalms'])
    expect(names('canticles')).toEqual(['Song of Solomon'])
    expect(names('1co')).toEqual(['1 Corinthians'])
  })

  it('is case- and punctuation-insensitive', () => {
    expect(names('GEN.')).toEqual(['Genesis'])
    expect(names('1 cor')).toEqual(['1 Corinthians'])
    expect(names('2 Tim')).toEqual(['2 Timothy'])
  })

  it('ranks an exact name above a name that merely contains the text', () => {
    // "John" is an exact name and also a variant of 1 John; the exact match wins,
    // and the alternative is still offered rather than hidden.
    expect(names('john')).toEqual(['John', '1 John'])
    expect(names('1 john')).toEqual(['1 John'])
  })

  it('keeps canonical order as the tie-break', () => {
    // Five books merely contain "o"; the canon decides between them.
    expect(names('o')).toEqual(['Song of Solomon', 'John', '1 Corinthians', '2 Timothy', '1 John'])
  })

  it('marks where the typed text lands in the name, and nothing when it does not', () => {
    const [genesis] = matchBooks('enes', BOOKS)
    expect(genesis.highlight).toEqual({ start: 1, length: 4 })

    // "1 cor" lands on "1 Corinthians" including the space between them.
    const [corinthians] = matchBooks('1 cor', BOOKS)
    expect(corinthians.highlight).toEqual({ start: 0, length: 5 })

    // An abbreviation is not part of the name, so there is nothing to mark.
    const [abbreviated] = matchBooks('gn', BOOKS)
    expect(abbreviated.highlight).toBeNull()
  })

  it('returns nothing for an empty or unmatched query', () => {
    expect(names('')).toEqual([])
    expect(names('   ')).toEqual([])
    expect(names('zzz')).toEqual([])
  })
})

describe('parseScriptureQuery', () => {
  it('reads a book name on its own', () => {
    const query = parseScriptureQuery('ge', BOOKS)

    expect(query.book?.name).toBe('Genesis')
    expect(query.coordinates).toBeNull()
    expect(query.error).toBeNull()
    expect(query.isComplete).toBe(false)
  })

  it('resolves "ge 1 1" to Genesis 1:1', () => {
    const query = parseScriptureQuery('ge 1 1', BOOKS)

    expect(query.coordinates).toEqual({ chapter: 1, verseStart: 1, verseEnd: null })
    expect(query.label).toBe('Genesis 1:1')
    expect(query.isComplete).toBe(true)
  })

  it('resolves "gen 1 1-3" to Genesis 1:1-3', () => {
    const query = parseScriptureQuery('gen 1 1-3', BOOKS)

    expect(query.coordinates).toEqual({ chapter: 1, verseStart: 1, verseEnd: 3 })
    expect(query.label).toBe('Genesis 1:1-3')
    expect(query.isComplete).toBe(true)
  })

  it('resolves "john 3:16"', () => {
    const query = parseScriptureQuery('john 3:16', BOOKS)

    expect(query.book?.name).toBe('John')
    expect(query.coordinates).toEqual({ chapter: 3, verseStart: 16, verseEnd: null })
    expect(query.label).toBe('John 3:16')
  })

  it('reads "ps 23" as the chapter, using the server’s chapter convention', () => {
    const query = parseScriptureQuery('ps 23', BOOKS)

    expect(query.book?.name).toBe('Psalms')
    expect(query.coordinates).toEqual({ chapter: 23, verseStart: null, verseEnd: null })
    expect(query.isComplete).toBe(true)
  })

  it('accepts spacing, colon, and range variations', () => {
    expect(parseScriptureQuery('1 cor 13:4-7', BOOKS).label).toBe('1 Corinthians 13:4-7')
    expect(parseScriptureQuery('1cor13', BOOKS).label).toBe('1 Corinthians 13')
    expect(parseScriptureQuery('john 3 16 18', BOOKS).label).toBe('John 3:16-18')
    expect(parseScriptureQuery('2 tim 3:16', BOOKS).label).toBe('2 Timothy 3:16')
    // A one-verse range is the same reference as naming the verse once.
    expect(parseScriptureQuery('john 3:16-16', BOOKS).coordinates).toEqual({
      chapter: 3,
      verseStart: 16,
      verseEnd: null,
    })
  })

  it('keeps a leading book number with the book', () => {
    const query = parseScriptureQuery('1 john 3:16', BOOKS)

    expect(query.bookText).toBe('1 john')
    expect(query.book?.name).toBe('1 John')
    expect(query.label).toBe('1 John 3:16')
  })

  it('reports a name it cannot match instead of resolving it to a neighbour', () => {
    const query = parseScriptureQuery('genesisish', BOOKS)

    expect(query.book).toBeNull()
    expect(query.error).toBe('No book matches "genesisish".')
    expect(query.isComplete).toBe(false)
  })

  it('reports a chapter beyond the book', () => {
    const query = parseScriptureQuery('gen 51', BOOKS)

    expect(query.coordinates).toBeNull()
    expect(query.error).toBe('Genesis has 50 chapters.')
  })

  it('reports chapter and verse numbers below one', () => {
    expect(parseScriptureQuery('gen 0', BOOKS).error).toBe('Chapter numbers start at 1.')
    expect(parseScriptureQuery('gen 1 0', BOOKS).error).toBe('Verse numbers start at 1.')
  })

  it('reports a reversed verse range', () => {
    expect(parseScriptureQuery('gen 1 16-15', BOOKS).error).toBe(
      'The verse range must start at or before its end.'
    )
  })

  it('refuses to guess at a range that could be chapters or verses', () => {
    const query = parseScriptureQuery('gen 1-3', BOOKS)

    expect(query.coordinates).toBeNull()
    expect(query.error).toContain('is ambiguous')
  })

  it('reports letters and extra numbers rather than dropping them', () => {
    expect(parseScriptureQuery('gen 1:1 x', BOOKS).error).toContain('Use a chapter and verse')
    expect(parseScriptureQuery('gen 1 1 2 3', BOOKS).error).toContain('Too many numbers')
  })

  it('treats a book number on its own as a book in progress', () => {
    // The numbered books are offered, and nothing is staged: "1" is not a
    // reference and the box is not told off for being half-typed.
    const numbered = parseScriptureQuery('1', BOOKS)
    expect(numbered.error).toBeNull()
    expect(numbered.coordinates).toBeNull()
    expect(numbered.isComplete).toBe(false)
    expect(numbered.matches.map(match => match.book.name)).toEqual(['1 Corinthians', '1 John'])

    // A digit that matches no book stays quiet too, rather than claiming the
    // operator typed something wrong.
    const quiet = parseScriptureQuery('9', BOOKS)
    expect(quiet.book).toBeNull()
    expect(quiet.error).toBeNull()
  })

  it('does nothing with empty input', () => {
    const query = parseScriptureQuery('   ', BOOKS)

    expect(query.book).toBeNull()
    expect(query.error).toBeNull()
    expect(query.matches).toEqual([])
  })

  it('matches nothing before the canon has arrived, and reports no name error', () => {
    // With no books loaded there is nothing to match against; the console shows
    // the loading hint rather than a failure.
    const query = parseScriptureQuery('john 3:16', [])

    expect(query.book).toBeNull()
    expect(query.matches).toEqual([])
    expect(query.error).toBeNull()
  })
})

describe('buildResults', () => {
  it('shows the resolved passage as the single result', () => {
    const { results, hiddenCount } = buildResults(parseScriptureQuery('ge 1 1', BOOKS))

    expect(hiddenCount).toBe(0)
    expect(results).toHaveLength(1)
    expect(results[0].kind).toBe('reference')
    expect(results[0].label).toBe('Genesis 1:1')
    expect(results[0].coordinates).toEqual({ chapter: 1, verseStart: 1, verseEnd: null })
  })

  it('names the chapter convention on a chapter-only result', () => {
    const { results } = buildResults(parseScriptureQuery('ps 23', BOOKS))

    expect(results[0].detail).toContain('Whole chapter')
  })

  it('lists matching books with their chapter counts', () => {
    const { results } = buildResults(parseScriptureQuery('gen', BOOKS))

    expect(results).toHaveLength(1)
    expect(results[0].kind).toBe('book')
    expect(results[0].label).toBe('Genesis')
    expect(results[0].detail).toBe('50 chapters')
  })

  it('caps the list and reports how many are hidden', () => {
    const books: ScriptureBook[] = Array.from({ length: 18 }, (_, index) => ({
      name: `Book ${index} an`,
      chapters: 3,
      order: index + 1,
    }))

    const { results, hiddenCount } = buildResults(parseScriptureQuery('an', books))
    expect(results).toHaveLength(BOOK_RESULT_LIMIT)
    expect(hiddenCount).toBe(18 - BOOK_RESULT_LIMIT)

    const all = buildResults(parseScriptureQuery('an', books), Number.POSITIVE_INFINITY)
    expect(all.results).toHaveLength(18)
    expect(all.hiddenCount).toBe(0)
  })
})
