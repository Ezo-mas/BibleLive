import { useCallback, useEffect, useState } from 'react'
import type { Passage } from '../../shared/types'
import { samePassage } from '../passageKey'

/** localStorage key for the operator's planned passages. */
export const PLAN_STORAGE_KEY = 'biblelive_operator_plan'

/** The most characters of verse text kept per item: enough to recognise it, small to store. */
const SNIPPET_CHARS = 200

/**
 * One planned passage: coordinates, plus the start of its text so the list is
 * recognisable at a glance. The text is a memory aid only — staging an item looks
 * the passage up again, and showing it sends coordinates for the server to resolve.
 */
export interface PlanItem {
  id: string
  book: string
  book_label: string | null
  chapter: number
  verse_start: number
  verse_end: number
  snippet: string
}

export interface PlanState {
  items: PlanItem[]
  /** Add a passage to the end of the plan; false when it is already planned. */
  add: (passage: Passage) => boolean
  remove: (id: string) => void
  /** Move an item one place up (-1) or down (+1). */
  move: (id: string, direction: -1 | 1) => void
  clear: () => void
}

function isPlanItem(value: unknown): value is PlanItem {
  if (typeof value !== 'object' || value === null) return false
  const item = value as Record<string, unknown>
  return (
    typeof item.id === 'string' &&
    typeof item.book === 'string' &&
    typeof item.chapter === 'number' &&
    typeof item.verse_start === 'number' &&
    typeof item.verse_end === 'number' &&
    typeof item.snippet === 'string'
  )
}

function readStoredPlan(): PlanItem[] {
  try {
    const parsed: unknown = JSON.parse(window.localStorage.getItem(PLAN_STORAGE_KEY) ?? '[]')
    return Array.isArray(parsed) ? parsed.filter(isPlanItem) : []
  } catch {
    // Storage blocked or the value is not JSON: start with an empty plan.
    return []
  }
}

let nextItemId = 0

function toItem(passage: Passage): PlanItem {
  nextItemId += 1
  return {
    id: `plan-${Date.now().toString(36)}-${nextItemId}`,
    book: passage.book,
    book_label: passage.book_label ?? null,
    chapter: passage.chapter,
    verse_start: passage.verse_start,
    verse_end: passage.verse_end,
    snippet: (passage.verses[0]?.text ?? passage.text).slice(0, SNIPPET_CHARS),
  }
}

/**
 * The passages the operator lined up before the service.
 *
 * Kept in this browser only: a plan is the operator's own preparation, not state
 * the server or the display needs. Nothing here touches the queue or the display;
 * an item reaches the screen through the same two doors a searched passage uses.
 */
export function usePlan(): PlanState {
  const [items, setItems] = useState<PlanItem[]>(readStoredPlan)

  useEffect(() => {
    try {
      window.localStorage.setItem(PLAN_STORAGE_KEY, JSON.stringify(items))
    } catch {
      // Private browsing can refuse storage: the plan still works for this page.
    }
  }, [items])

  const add = useCallback(
    (passage: Passage) => {
      if (items.some(item => samePassage(item, passage))) return false
      setItems(prev => [...prev, toItem(passage)])
      return true
    },
    [items]
  )

  const remove = useCallback((id: string) => {
    setItems(prev => prev.filter(item => item.id !== id))
  }, [])

  const move = useCallback((id: string, direction: -1 | 1) => {
    setItems(prev => {
      const from = prev.findIndex(item => item.id === id)
      const to = from + direction
      if (from === -1 || to < 0 || to >= prev.length) return prev
      const next = [...prev]
      ;[next[from], next[to]] = [next[to], next[from]]
      return next
    })
  }, [])

  const clear = useCallback(() => setItems([]), [])

  return { items, add, remove, move, clear }
}
