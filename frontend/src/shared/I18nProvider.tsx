import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import {
  fetchServerLanguage,
  readStoredLanguage,
  resolveLanguage,
  translatorFor,
  writeStoredLanguage,
} from './i18n'
import type { Language } from './i18n'
import { I18nContext } from './i18nContext'
import type { I18nValue } from './i18nContext'
import { readStoredOperatorToken } from './operatorToken'

/**
 * Supplies the console's language to everything below it.
 *
 * It renders at once with the best language already known (this browser's choice,
 * else the browser's own language) and switches when the launcher's answer
 * arrives, so nothing waits on the network. The server is asked on mount and again
 * whenever the operator console connects, because the token that unlocks the
 * answer may only be entered after the first request.
 */
export function I18nProvider({ children }: { children: ReactNode }) {
  const [override, setOverride] = useState<Language | null>(readStoredLanguage)
  const [server, setServer] = useState<Language | null>(null)
  // Read once: the browser's language does not change under a running page.
  const [browser] = useState(() => (typeof navigator === 'undefined' ? '' : navigator.language))
  const language = resolveLanguage({ override, server, browser })

  // Each request is numbered so a slow answer cannot overwrite a newer one.
  const requestRef = useRef(0)
  const refreshServerLanguage = useCallback(() => {
    const id = (requestRef.current += 1)
    void fetchServerLanguage(readStoredOperatorToken()).then(answer => {
      if (id === requestRef.current) setServer(answer)
    })
  }, [])

  useEffect(() => {
    refreshServerLanguage()
  }, [refreshServerLanguage])

  // Screen readers and hyphenation follow the page language.
  useEffect(() => {
    document.documentElement.lang = language
  }, [language])

  const setLanguage = useCallback((next: Language | null) => {
    writeStoredLanguage(next)
    setOverride(next)
  }, [])

  const value = useMemo<I18nValue>(
    () => ({
      t: translatorFor(language),
      language,
      override,
      setLanguage,
      refreshServerLanguage,
    }),
    [language, override, setLanguage, refreshServerLanguage]
  )

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>
}
