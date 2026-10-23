import { useEffect, useState } from 'react'
import { lookupPassage } from '../passageApi'
import { useI18n } from '../../shared/i18nContext'
import type { PassageQuery } from '../passageApi'

/** The most characters of looked-up text kept: enough for a couple of lines. */
const TEXT_CHARS = 240

/**
 * The text of one passage, read through the existing read-only lookup (the one
 * behind the search verse-start preview), for a card that names a passage but does
 * not carry its words — the follow-along suggestion.
 *
 * Null until the text arrives, and null for good if the lookup fails or the
 * console is not connected: the card then shows the reference alone and nothing
 * else changes. A newer passage supersedes an older one still in flight, so a late
 * answer can never fill the card for the wrong passage. Sends nothing to the
 * display; it only reads.
 */
export function usePassageText(
  query: PassageQuery | null,
  token: string | null,
  enabled: boolean
): string | null {
  const { t } = useI18n()
  const [result, setResult] = useState<{ key: string; text: string } | null>(null)
  const key = query
    ? `${query.book}|${query.chapter}|${query.verseStart}|${query.verseEnd}|${String(enabled)}`
    : null
  const { book, chapter, verseStart, verseEnd } = query ?? {
    book: '',
    chapter: 0,
    verseStart: null,
    verseEnd: null,
  }

  useEffect(() => {
    if (key === null || !enabled) return
    let current = true
    void lookupPassage({ book, chapter, verseStart, verseEnd }, token, t)
      .then(passage => {
        const text = (passage.text || passage.verses[0]?.text || '').slice(0, TEXT_CHARS)
        if (current && text) setResult({ key, text })
      })
      .catch(() => {
        // The reference stands on its own.
      })
    return () => {
      current = false
    }
  }, [key, enabled, book, chapter, verseStart, verseEnd, token, t])

  return result !== null && result.key === key ? result.text : null
}
