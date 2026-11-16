import { useEffect, useRef, useState } from 'react'
import { lookupPassage } from '../passageApi'
import { useI18n } from '../../shared/i18nContext'
import type { PlanItem } from './usePlan'

/** The most characters of looked-up text kept per item, as when an item is added. */
const SNIPPET_CHARS = 200

/**
 * Text for plan items that were saved without any.
 *
 * Items normally carry the start of their passage from the moment they are
 * planned, but an older saved plan or a failed lookup can leave one empty. This
 * asks the existing read-only lookup (the one behind the search verse-start
 * preview) while the console is connected, and returns the results by item id.
 * Failed lookups retry once. A confirmed translation change starts fresh and
 * looks up every item again, including those saved with text, so no row keeps
 * the old translation; the looked-up text then wins over the saved one.
 */
export function usePlanSnippets(
  items: readonly PlanItem[],
  token: string | null,
  enabled: boolean,
  version: string | null = null
): Record<string, string> {
  const { t } = useI18n()
  const [snippets, setSnippets] = useState<Record<string, string>>({})
  const asked = useRef(new Set<string>())
  const versionRef = useRef(version)
  const generation = useRef(0)
  // Set once the translation has changed: saved text is in the old one from then on.
  const refetchSaved = useRef(false)

  useEffect(() => {
    if (version === null) return
    if (versionRef.current !== null && versionRef.current !== version) {
      generation.current += 1
      asked.current.clear()
      refetchSaved.current = true
      setSnippets({})
    }
    versionRef.current = version
  }, [version])

  useEffect(
    () => () => {
      generation.current += 1
    },
    []
  )

  useEffect(() => {
    if (!enabled) return
    for (const item of items) {
      if ((item.snippet && !refetchSaved.current) || asked.current.has(item.id)) continue
      asked.current.add(item.id)
      const currentGeneration = generation.current
      const lookup = () =>
        lookupPassage(
          {
            book: item.book,
            chapter: item.chapter,
            verseStart: item.verse_start,
            verseEnd: item.verse_end,
          },
          token,
          t
        )
      void lookup()
        .catch(() => {
          if (currentGeneration === generation.current) return lookup()
        })
        .then(passage => {
          if (!passage || currentGeneration !== generation.current) return
          const text = (passage.verses[0]?.text ?? passage.text ?? '').slice(0, SNIPPET_CHARS)
          if (text) setSnippets(prev => ({ ...prev, [item.id]: text }))
        })
        .catch(() => {
          // The reference stands on its own until the text arrives.
        })
    }
  }, [items, token, enabled, t, version])

  return snippets
}
