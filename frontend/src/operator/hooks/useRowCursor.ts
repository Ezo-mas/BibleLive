import { useCallback, useEffect, useRef, useState } from 'react'
import type { RefObject } from 'react'
import type { CursorList, OperatorEvent, OperatorReducerState } from '../state/operatorTypes'
import type { PlanItem } from './usePlan'

export interface RowCursorState {
  /** The highlighted detection in Pending, or null when the cursor is not there. */
  pendingCursorId: string | null
  /** The highlighted entry in Plan, or null when the cursor is not there (or it was removed). */
  planCursorId: string | null
  /** Keyboard moves so far; a move hands focus to the row it lands on (see useCursorFocus). */
  moves: number
  /** Highlight a row: a click or focus on it. Nothing is sent. */
  highlight: (list: CursorList, id: string) => void
  /** Move the highlight one row; `pendingVisible` is whether the Pending tab is open. */
  move: (step: number, pendingVisible: boolean) => void
  /** Whether Enter would show something now (a highlight in a list that is on screen). */
  canShow: (pendingVisible: boolean) => boolean
  /** Enter: show the highlighted row through its own door. */
  show: (pendingVisible: boolean) => void
  /** Escape: drop the highlight. */
  clear: () => void
}

/**
 * The console's one keyboard cursor, in Pending or Plan.
 *
 * A click (or focus) on a row highlights it and sends nothing; Enter shows the
 * highlighted row, just like double-clicking the row. The cursor is in the list the
 * operator last clicked or focused, so the arrow keys and Enter act there. With no
 * cursor, the arrows start in Pending while its tab is open and holds detections,
 * otherwise in Plan. A highlight in Pending is inert while the History tab covers it:
 * Enter never shows a row the operator cannot see. An arrow also moves focus onto the
 * highlighted row (useCursorFocus), so focus and the cursor never part.
 */
export function useRowCursor(
  state: OperatorReducerState,
  dispatch: (event: OperatorEvent) => void,
  planItems: PlanItem[],
  showPending: (id: string) => void,
  showPlanItem: (item: PlanItem) => void
): RowCursorState {
  const { cursor, pending } = state
  const pendingCursorId = cursor?.list === 'pending' ? cursor.id : null
  const planCursorId =
    cursor?.list === 'plan' && planItems.some(item => item.id === cursor.id) ? cursor.id : null
  const [moves, setMoves] = useState(0)

  const highlight = useCallback(
    (list: CursorList, id: string) => dispatch({ type: 'set_cursor', cursor: { list, id } }),
    [dispatch]
  )

  const move = useCallback(
    (step: number, pendingVisible: boolean) => {
      const list: CursorList =
        pendingCursorId !== null || (planCursorId === null && pendingVisible && pending.length > 0)
          ? 'pending'
          : 'plan'
      if (list === 'pending' && !pendingVisible) return
      setMoves(count => count + 1)
      dispatch({ type: 'move_cursor', list, step, planIds: planItems.map(item => item.id) })
    },
    [pendingCursorId, planCursorId, pending.length, planItems, dispatch]
  )

  const canShow = useCallback(
    (pendingVisible: boolean) =>
      (pendingCursorId !== null && pendingVisible) || planCursorId !== null,
    [pendingCursorId, planCursorId]
  )

  const show = useCallback(
    (pendingVisible: boolean) => {
      if (pendingCursorId !== null) {
        if (pendingVisible) showPending(pendingCursorId)
        return
      }
      const item = planItems.find(entry => entry.id === planCursorId)
      if (item) showPlanItem(item)
    },
    [pendingCursorId, planCursorId, planItems, showPending, showPlanItem]
  )

  const clear = useCallback(() => dispatch({ type: 'set_cursor', cursor: null }), [dispatch])

  return { pendingCursorId, planCursorId, moves, highlight, move, canShow, show, clear }
}

/**
 * Roving focus for one list of rows: after a keyboard move, the highlighted row takes
 * focus and scrolls into view, so the cursor, focus and the next Enter all sit on the
 * same row. A row marks itself with `data-cursor-row`, and its `role="button"` element
 * is the one that takes focus.
 *
 * Only a keyboard move does this. A click, a new detection or a server update changes
 * the highlight and leaves focus where it is. The keyboard shortcuts already stay out
 * of inputs, buttons, selects and an open dialog, so a move never takes focus from them.
 */
export function useCursorFocus(
  listRef: RefObject<HTMLElement>,
  selectedId: string | null,
  moves: number
): void {
  const seen = useRef(moves)

  useEffect(() => {
    if (moves === seen.current) return
    seen.current = moves
    if (selectedId === null) return
    const rows = Array.from(
      listRef.current?.querySelectorAll<HTMLElement>('[data-cursor-row]') ?? []
    )
    const row = rows.find(element => element.dataset.cursorRow === selectedId)
    if (!row) return
    row.querySelector<HTMLElement>('[role="button"]')?.focus({ preventScroll: true })
    row.scrollIntoView({ block: 'nearest' })
  }, [listRef, selectedId, moves])
}
