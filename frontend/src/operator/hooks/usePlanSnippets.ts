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
 * preview) once per such item, while the console is connected, and returns the
 * results by item id. A slow or failed lookup is ignored: the row simply keeps
 * showing the reference alone, and it is not asked again until the page reloads.
 */
export function usePlanSnippets(
  items: readonly PlanItem[],
  token: string | null,
  enabled: boolean
): Record<string, string> {
  const { t } = useI18n()
  const [snippets, setSnippets] = useState<Record<string, string>>({})
  const asked = useRef(new Set<string>())

  useEffect(() => {
    if (!enabled) return
    for (const item of items) {
      if (item.snippet || asked.current.has(item.id)) continue
      asked.current.add(item.id)
      void lookupPassage(
        {
          book: item.book,
          chapter: item.chapter,
          verseStart: item.verse_start,
          verseEnd: item.verse_end,
        },
        token,
        t
      )
        .then(passage => {
          const text = (passage.verses[0]?.text ?? passage.text ?? '').slice(0, SNIPPET_CHARS)
          if (text) setSnippets(prev => ({ ...prev, [item.id]: text }))
        })
        .catch(() => {
          // The reference stands on its own until the text arrives.
        })
    }
  }, [items, token, enabled, t])

  return snippets
}
