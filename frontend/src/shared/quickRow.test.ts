import { describe, expect, it } from 'vitest'
import { en } from './messages.en'
import { lt } from './messages.lt'

/**
 * The control row under the display preview is one line down to the column's
 * floor (560px) in both languages. These are the strings that set its width, so a
 * longer translation has to be a conscious choice, and checked in the browser.
 */
describe('control row strings', () => {
  const LANGS = { en, lt } as const

  it.each(Object.entries(LANGS))(
    '%s: captions are short enough to sit over their control',
    (_n, m) => {
      // An overline caption is about 9px a character; the row has room for ~15 in a group.
      for (const key of ['quick.screenLabel', 'quick.versesLabel', 'quick.autoCaption'] as const) {
        expect(String(m[key]).length, key).toBeLessThanOrEqual(15)
      }
    }
  )

  it.each(Object.entries(LANGS))('%s: the Off / On options fit a 44px option', (_n, m) => {
    for (const key of ['quick.autoOff', 'quick.autoOn'] as const) {
      expect(String(m[key]).length, key).toBeLessThanOrEqual(4)
    }
  })

  it('keeps the full names for the accessible group, apart from the short captions', () => {
    expect(en['quick.autoLabel']).toBe('Auto-approve')
    expect(lt['quick.autoLabel']).toBe('Automatinis patvirtinimas')
    expect(lt['quick.autoCaption']).not.toBe(lt['quick.autoLabel'])
  })
})
