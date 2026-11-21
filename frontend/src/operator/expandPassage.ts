// Keep in sync with FOLLOW_ALONG_WINDOW in backend/core/server/follow_along.py.
const FOLLOW_ALONG_WINDOW = 3

export function expandPassage(verseStart: number, verseEnd: number | null) {
  const nextEnd = (verseEnd ?? verseStart) + 1
  return {
    verseStart: Math.max(verseStart, nextEnd - FOLLOW_ALONG_WINDOW + 1),
    verseEnd: nextEnd,
  }
}
