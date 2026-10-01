import type { BibleReference } from './types'

/**
 * Format a BibleReference for display, e.g. "John 3:16" or "Psalms 23:1-6".
 * Verse numbers are omitted when the reference is chapter-level only.
 * Uses loose null checks since the data comes from unvalidated JSON.
 */
export function formatReference(ref: BibleReference): string {
  let out = `${ref.book} ${ref.chapter}`
  if (ref.verse_start != null) {
    out += `:${ref.verse_start}`
    if (ref.verse_end != null && ref.verse_end !== ref.verse_start) {
      out += `-${ref.verse_end}`
    }
  }
  return out
}
