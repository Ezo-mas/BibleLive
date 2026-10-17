import type { BibleReference } from './types'

/**
 * The book name to show: the server's name in the spoken language when it sent
 * one, otherwise the canonical name. Display only — lookups use `book`.
 */
export function bookDisplayName(ref: { book: string; book_label?: string | null }): string {
  return ref.book_label || ref.book
}

/**
 * Format a BibleReference for display, e.g. "John 3:16" or "Psalms 23:1-6".
 * Verse numbers are omitted when the reference is chapter-level only.
 * Uses loose null checks since the data comes from unvalidated JSON.
 */
export function formatReference(ref: BibleReference): string {
  let out = `${bookDisplayName(ref)} ${ref.chapter}`
  if (ref.verse_start != null) {
    out += `:${ref.verse_start}`
    if (ref.verse_end != null && ref.verse_end !== ref.verse_start) {
      out += `-${ref.verse_end}`
    }
  }
  return out
}
