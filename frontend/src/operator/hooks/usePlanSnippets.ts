import { useEffect, useRef, useState } from 'react'
import { lookupPassage } from '../passageApi'
import { useI18n } from '../../shared/i18nContext'
import { planExcerpt } from './usePlan'
import type { PlanItem } from './usePlan'

/**
 * Text for plan items missing a passage excerpt.
 *
 * Items normally carry the start of their passage from the moment they are
 * planned. Older ranges saved only the first verse. This fills out those excerpts
 * and empty ones using the existing read-only lookup while connected.
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
      const hasExcerpt =
        item.snippet && (item.verse_start === item.verse_end || item.snippetFormat === 'passage')
      if ((hasExcerpt && !refetchSaved.current) || asked.current.has(item.id)) continue
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
          const text = planExcerpt(passage)
          if (text) setSnippets(prev => ({ ...prev, [item.id]: text }))
        })
        .catch(() => {
          // The row can use its saved text or show just the reference.
        })
    }
  }, [items, token, enabled, t, version])

  return snippets
}
