import type { BibleReference } from '../shared/types'

interface Coordinates {
  book: string
  chapter: number
  verse_start: number | null
  verse_end: number | null
}

/**
 * The verse a passage covers at its right end.
 *
 * A single-verse reference reaches the console in two shapes: a detection that
 * named one verse is stored as-is (``verse_end: null``), while the server states
 * the same passage as ``verse_start === verse_end`` when it resolves one. They
 * are the same passage, and only comparing them shape-blind would make a replay
 * of a detected verse look like someone else's.
 */
export function referenceEnd(reference: {
  verse_start: number | null
  verse_end: number | null
}): number | null {
  return reference.verse_end ?? reference.verse_start
}

/** Whether two references point at the same book, chapter and verses. */
export function samePassage(left: Coordinates, right: Coordinates): boolean {
  return (
    left.book === right.book &&
    left.chapter === right.chapter &&
    left.verse_start === right.verse_start &&
    referenceEnd(left) === referenceEnd(right)
  )
}

/**
 * Keep one history entry per passage: the newest, which is first in the list.
 *
 * Showing a passage again, or a detection of the same verse, used to stack
 * identical rows and push everything else out of the window.
 */
export function dedupeHistory(entries: BibleReference[]): BibleReference[] {
  const kept: BibleReference[] = []
  for (const entry of entries) {
    if (!kept.some(other => samePassage(other, entry))) kept.push(entry)
  }
  return kept
}
