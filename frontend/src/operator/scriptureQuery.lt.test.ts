import { describe, expect, it } from 'vitest'
import { buildResults, parseScriptureQuery } from './scriptureQuery'
import { translatorFor } from '../shared/i18n'
import type { ScriptureBook } from '../shared/types'

const GENESIS: ScriptureBook = { name: 'Genesis', chapters: 50, order: 1, abbrev: 'gn' }
const JOHN: ScriptureBook = { name: 'John', chapters: 21, order: 43, abbrev: 'jn' }
const BOOKS = [GENESIS, JOHN]

const lt = translatorFor('lt')

describe('scripture query messages in Lithuanian', () => {
  it('translates the errors the operator sees while typing', () => {
    expect(parseScriptureQuery('gen 0', BOOKS, lt).error).toBe('Skyrių numeriai prasideda nuo 1.')
    expect(parseScriptureQuery('gen 1 0', BOOKS, lt).error).toBe(
      'Eilučių numeriai prasideda nuo 1.'
    )
    expect(parseScriptureQuery('gen 1 1 2 3', BOOKS, lt).error).toBe(
      'Per daug skaičių — nurodykite skyrių ir eilutę, pavyzdžiui „Genesis 3:16-18“.'
    )
    // Digits alone are someone still typing a book number, so they stay quiet.
    expect(parseScriptureQuery('9', BOOKS, lt).error).toBeNull()
    expect(parseScriptureQuery('zzzq 1', BOOKS, lt).error).toBe(
      'Nėra knygos, atitinkančios „zzzq 1“.'
    )
  })

  it('pluralises the chapter count of a book that is out of range', () => {
    expect(parseScriptureQuery('john 22', BOOKS, lt).error).toBe('John turi 21 skyrių.')
    expect(parseScriptureQuery('john 22', BOOKS, translatorFor('en')).error).toBe(
      'John has 21 chapters.'
    )
  })

  it('translates the book-row detail and the whole-chapter note', () => {
    const books = buildResults(parseScriptureQuery('ge', BOOKS, lt), undefined, lt).results
    expect(books[0].detail).toBe('50 skyrių')

    const chapter = buildResults(parseScriptureQuery('gen 2', BOOKS, lt), undefined, lt).results
    expect(chapter[0].detail).toBe('Visas skyrius — rodoma nuo 1 eilutės')
  })

  it('keeps English as the default when no translator is passed', () => {
    expect(parseScriptureQuery('gen 0', BOOKS).error).toBe('Chapter numbers start at 1.')
  })
})
