import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { I18nProvider } from './I18nProvider'
import { useI18n } from './i18nContext'
import { LANGUAGE_STORAGE_KEY } from './i18n'

/** Shows what the console would render, and offers the controls the provider exposes. */
function Probe() {
  const { t, language, override, setLanguage, refreshServerLanguage } = useI18n()
  return (
    <>
      <p data-testid="text">{t('stage.onDisplay')}</p>
      <p data-testid="language">{language}</p>
      <p data-testid="override">{override ?? 'none'}</p>
      <button onClick={() => setLanguage('en')}>choose-en</button>
      <button onClick={() => setLanguage(null)}>choose-auto</button>
      <button onClick={() => refreshServerLanguage()}>refresh</button>
    </>
  )
}

/** A launcher answer the test resolves by hand, so it can land at any moment. */
interface Pending {
  resolve: (language: string | null) => void
}

let pending: Pending[] = []

function stubLauncher() {
  pending = []
  vi.stubGlobal(
    'fetch',
    vi.fn(
      () =>
        new Promise<Response>(resolve => {
          pending.push({
            resolve: language =>
              resolve({
                ok: true,
                status: 200,
                json: async () => ({ language }),
              } as unknown as Response),
          })
        })
    )
  )
}

/** Answer one outstanding launcher request (the oldest unless `index` says otherwise). */
async function answer(language: string | null, index = 0) {
  const [next] = pending.splice(index, 1)
  if (!next) throw new Error('no launcher request is waiting')
  await act(async () => {
    next.resolve(language)
  })
}

function setBrowserLanguage(value: string) {
  Object.defineProperty(window.navigator, 'language', { value, configurable: true })
}

function text() {
  return screen.getByTestId('text').textContent
}

function language() {
  return screen.getByTestId('language').textContent
}

beforeEach(() => {
  window.localStorage.clear()
  setBrowserLanguage('en-US')
  stubLauncher()
})

afterEach(() => {
  Reflect.deleteProperty(window.navigator, 'language')
  vi.unstubAllGlobals()
  document.documentElement.lang = 'en'
})

describe('I18nProvider', () => {
  it('renders at once in the browser language, without waiting for the launcher', () => {
    setBrowserLanguage('lt-LT')
    render(
      <I18nProvider>
        <Probe />
      </I18nProvider>
    )
    expect(language()).toBe('lt')
    expect(text()).toBe('Ekrane')
    // The request is still outstanding; the page did not wait for it.
    expect(pending).toHaveLength(1)
  })

  it('switches to the launcher setting when it arrives', async () => {
    render(
      <I18nProvider>
        <Probe />
      </I18nProvider>
    )
    expect(language()).toBe('en')
    await answer('lt')
    expect(language()).toBe('lt')
    expect(text()).toBe('Ekrane')
  })

  it('keeps the operator choice over the launcher setting', async () => {
    window.localStorage.setItem(LANGUAGE_STORAGE_KEY, 'en')
    render(
      <I18nProvider>
        <Probe />
      </I18nProvider>
    )
    await answer('lt')
    expect(language()).toBe('en')
    expect(screen.getByTestId('override').textContent).toBe('en')
  })

  it('uses the browser language when the launcher gives no answer', async () => {
    setBrowserLanguage('lt-LT')
    render(
      <I18nProvider>
        <Probe />
      </I18nProvider>
    )
    await answer(null)
    expect(language()).toBe('lt')
  })

  it('prefers the launcher setting to the browser language', async () => {
    setBrowserLanguage('lt-LT')
    render(
      <I18nProvider>
        <Probe />
      </I18nProvider>
    )
    await answer('en')
    expect(language()).toBe('en')
  })

  it('writes a choice when one is made, and Auto forgets it so the launcher decides again', async () => {
    render(
      <I18nProvider>
        <Probe />
      </I18nProvider>
    )
    await answer('lt')
    fireEvent.click(screen.getByText('choose-en'))
    expect(language()).toBe('en')
    expect(window.localStorage.getItem(LANGUAGE_STORAGE_KEY)).toBe('en')

    fireEvent.click(screen.getByText('choose-auto'))
    expect(window.localStorage.getItem(LANGUAGE_STORAGE_KEY)).toBeNull()
    expect(language()).toBe('lt')
  })

  it('asks the launcher again when refreshed, and lets only the newest answer count', async () => {
    render(
      <I18nProvider>
        <Probe />
      </I18nProvider>
    )
    fireEvent.click(screen.getByText('refresh'))
    expect(pending).toHaveLength(2)
    // The newer request (index 1) answers first; the older one arrives late.
    await answer('en', 1)
    await answer('lt', 0)
    expect(language()).toBe('en')
  })

  it('sets the document language to the one in use', async () => {
    render(
      <I18nProvider>
        <Probe />
      </I18nProvider>
    )
    expect(document.documentElement.lang).toBe('en')
    await answer('lt')
    await waitFor(() => expect(document.documentElement.lang).toBe('lt'))
  })
})
