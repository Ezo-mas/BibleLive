import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  LANGUAGE_STORAGE_KEY,
  defaultTranslator,
  fetchServerLanguage,
  hasMessage,
  pluralCategory,
  readStoredLanguage,
  resolveLanguage,
  translatorFor,
  writeStoredLanguage,
} from './i18n'
import { en } from './messages.en'
import { lt } from './messages.lt'
import { OPERATOR_TOKEN_STORAGE_KEY } from './operatorToken'

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  window.localStorage.clear()
})

describe('pluralCategory', () => {
  it.each([
    [1, 'one'],
    [2, 'few'],
    [5, 'few'],
    [10, 'other'],
    [11, 'other'],
    [12, 'other'],
    [21, 'one'],
    [22, 'few'],
    [25, 'few'],
    [111, 'other'],
  ] as const)('Lithuanian count %i takes the %s form', (count, form) => {
    expect(pluralCategory('lt', count)).toBe(form)
  })

  it('gives Lithuanian 101 the one form, and 112 the other form', () => {
    expect(pluralCategory('lt', 101)).toBe('one')
    expect(pluralCategory('lt', 112)).toBe('other')
  })

  it('gives English only one and other', () => {
    expect(pluralCategory('en', 1)).toBe('one')
    expect(pluralCategory('en', 0)).toBe('other')
    expect(pluralCategory('en', 2)).toBe('other')
    expect(pluralCategory('en', 21)).toBe('other')
  })
})

describe('translatorFor', () => {
  it('fills {name} placeholders from the params', () => {
    expect(translatorFor('en')('notice.shown', { ref: 'John 3:16' })).toBe('Shown: John 3:16')
    expect(translatorFor('lt')('notice.shown', { ref: 'Jono 3:16' })).toBe('Rodoma: Jono 3:16')
  })

  it('leaves a placeholder without a value visible rather than hiding it', () => {
    expect(translatorFor('en')('notice.shown')).toBe('Shown: {ref}')
  })

  it('picks the Lithuanian plural form from count', () => {
    const t = translatorFor('lt')
    expect(t('stage.moreVerses', { count: 1 })).toBe('dar 1 eilutė')
    expect(t('stage.moreVerses', { count: 3 })).toBe('dar 3 eilutės')
    expect(t('stage.moreVerses', { count: 11 })).toBe('dar 11 eilučių')
  })

  it('picks the English plural form from count', () => {
    const t = translatorFor('en')
    expect(t('ref.verses', { count: 1 })).toBe('1 verse')
    expect(t('ref.verses', { count: 2 })).toBe('2 verses')
  })

  it('uses the default translator in English', () => {
    expect(defaultTranslator('queue.empty')).toBe('No pending references')
  })

  it('recognises only real message keys', () => {
    expect(hasMessage('pipeline.issue.mic_silent')).toBe(true)
    expect(hasMessage('pipeline.issue.not_a_code')).toBe(false)
    expect(hasMessage('constructor')).toBe(false)
  })

  it('uses the same {placeholders} in every language, so no value is lost in translation', () => {
    const placeholders = (entry: unknown): string[] => {
      const forms = typeof entry === 'string' ? [entry] : Object.values(entry as object)
      // A plural entry repeats its placeholders in every form, so each name counts once.
      return [
        ...new Set(
          forms.flatMap(form => [...(form as string).matchAll(/\{(\w+)\}/g)].map(match => match[1]))
        ),
      ].sort()
    }
    for (const key of Object.keys(en) as (keyof typeof en)[]) {
      expect(placeholders(lt[key]), key).toEqual(placeholders(en[key]))
    }
  })
})

describe('resolveLanguage', () => {
  it('prefers the operator choice, then the launcher, then the browser, then English', () => {
    expect(resolveLanguage({ override: 'en', server: 'lt', browser: 'lt-LT' })).toBe('en')
    expect(resolveLanguage({ override: 'lt', server: 'en', browser: 'en-US' })).toBe('lt')
    expect(resolveLanguage({ override: null, server: 'lt', browser: 'en-US' })).toBe('lt')
    expect(resolveLanguage({ override: null, server: 'en', browser: 'lt-LT' })).toBe('en')
    expect(resolveLanguage({ override: null, server: null, browser: 'lt-LT' })).toBe('lt')
    expect(resolveLanguage({ override: null, server: null, browser: 'en-US' })).toBe('en')
  })

  it('reads a Lithuanian browser tag by its language part only', () => {
    expect(resolveLanguage({ override: null, server: null, browser: 'lt' })).toBe('lt')
    expect(resolveLanguage({ override: null, server: null, browser: 'LT-lt' })).toBe('lt')
    expect(resolveLanguage({ override: null, server: null, browser: 'ltx' })).toBe('en')
    expect(resolveLanguage({ override: null, server: null, browser: '' })).toBe('en')
  })
})

describe('the stored operator choice', () => {
  it('round-trips a language and forgets it on null', () => {
    writeStoredLanguage('lt')
    expect(window.localStorage.getItem(LANGUAGE_STORAGE_KEY)).toBe('lt')
    expect(readStoredLanguage()).toBe('lt')
    writeStoredLanguage(null)
    expect(readStoredLanguage()).toBeNull()
  })

  it('ignores a stored value that is not a language', () => {
    window.localStorage.setItem(LANGUAGE_STORAGE_KEY, 'fr')
    expect(readStoredLanguage()).toBeNull()
  })

  it('works without storage: no choice is read and nothing throws', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('storage blocked')
    })
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('storage blocked')
    })
    expect(readStoredLanguage()).toBeNull()
    expect(() => writeStoredLanguage('en')).not.toThrow()
  })
})

describe('fetchServerLanguage', () => {
  function stubFetch(impl: (url: string, init?: RequestInit) => Promise<Response>) {
    const fetchMock = vi.fn(impl)
    vi.stubGlobal('fetch', fetchMock)
    return fetchMock
  }

  function json(body: unknown, status = 200): Response {
    return {
      ok: status >= 200 && status < 300,
      status,
      json: async () => body,
    } as unknown as Response
  }

  it('returns the launcher language and sends no operator token, even if saved', async () => {
    window.localStorage.setItem(OPERATOR_TOKEN_STORAGE_KEY, 'secret')
    const fetchMock = stubFetch(async () => json({ language: 'lt' }))
    await expect(fetchServerLanguage()).resolves.toBe('lt')
    // Called with the URL alone: no init, so no Authorization header.
    expect(fetchMock).toHaveBeenCalledWith('/api/ui-language')
  })

  it('sends no Authorization header when no token is saved', async () => {
    const fetchMock = stubFetch(async () => json({ language: null }))
    await expect(fetchServerLanguage()).resolves.toBeNull()
    expect(fetchMock).toHaveBeenCalledWith('/api/ui-language')
  })

  it('treats a refusal, an unknown language, and a dropped request as no answer', async () => {
    stubFetch(async () => json({ detail: 'nope' }, 401))
    await expect(fetchServerLanguage()).resolves.toBeNull()

    stubFetch(async () => json({ language: 'fr' }))
    await expect(fetchServerLanguage()).resolves.toBeNull()

    stubFetch(async () => {
      throw new TypeError('network down')
    })
    await expect(fetchServerLanguage()).resolves.toBeNull()
  })
})
