import { createContext, useContext } from 'react'
import { defaultTranslator } from './i18n'
import type { Language, Translator } from './i18n'

export interface I18nValue {
  /** Translate a message key into the console's language. */
  t: Translator
  language: Language
  /** This browser's own choice, or null when it follows the launcher and the browser. */
  override: Language | null
  /** Choose a language for this browser; null forgets the choice and follows the launcher. */
  setLanguage: (language: Language | null) => void
  /** Ask the server for the launcher's language again (the console does this on connect). */
  refreshServerLanguage: () => void
}

/**
 * Without a provider the console is English. `renderApp` always supplies one, so
 * this default only matters for a component rendered on its own, such as in a test.
 */
const defaultValue: I18nValue = {
  t: defaultTranslator,
  language: 'en',
  override: null,
  setLanguage: () => {},
  refreshServerLanguage: () => {},
}

export const I18nContext = createContext<I18nValue>(defaultValue)

/** The console's translator and language setting. */
export function useI18n(): I18nValue {
  return useContext(I18nContext)
}
