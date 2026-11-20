import { useEffect, useMemo } from 'react'
import type { ReactNode } from 'react'
import { I18nContext } from '../shared/i18nContext'
import { translatorFor } from '../shared/i18n'
import type { Language } from '../shared/i18n'

export function LauncherI18nProvider({
  language,
  children,
}: {
  language: Language
  children: ReactNode
}) {
  useEffect(() => {
    document.documentElement.lang = language
    document.title = translatorFor(language)('launcher.windowTitle')
  }, [language])

  const value = useMemo(
    () => ({
      language,
      t: translatorFor(language),
      override: null,
      setLanguage: () => {},
      refreshServerLanguage: () => {},
    }),
    [language]
  )

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>
}
