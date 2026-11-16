import { useCallback, useRef } from 'react'
import { useI18n } from '../../shared/i18nContext'
import { formatReference } from '../../shared/reference'
import type { BibleReference, Passage } from '../../shared/types'
import { passageToReference } from '../passageApi'
import type { PassageQuery } from '../passageApi'
import { samePassage } from '../passageKey'
import type { OperatorEvent, OperatorReducerState, StagedReference } from '../state/operatorTypes'
import type { NoticeTone } from './useNotices'
import { usePlan } from './usePlan'
import type { PlanItem } from './usePlan'
import { useScriptureSearch } from './useScriptureSearch'

/** Where the staged scripture came from, for the panel's badge. */
export type NextSource = 'detected' | 'manual' | 'history' | null

/** How long a click counts as the start of a double-click, for restoring Up Next. */
const DOUBLE_CLICK_MS = 500

/** Local staging, lookup, and double-click gestures; display requests remain reducer intents. */
export function useStaging(
  state: OperatorReducerState,
  dispatch: (event: OperatorEvent) => void,
  token: string | null,
  isConnected: boolean,
  bibleVersion: string | null,
  showNotice: (tone: NoticeTone, text: string) => void
) {
  const { t } = useI18n()
  const { staged, pending, history, showInFlight } = state
  const stagedRef = useRef(staged)
  stagedRef.current = staged
  // A double-click is click, click, double-click, and each click stages its row. The
  // double-click is meant to skip staging, so it puts back what Up Next held before
  // the gesture began instead of leaving it emptied by the row it just showed.
  const gestureRef = useRef<{ key: string; before: StagedReference | null; at: number } | null>(
    null
  )
  const beginGesture = useCallback((key: string) => {
    const now = Date.now()
    const current = gestureRef.current
    if (current && current.key === key && now - current.at < DOUBLE_CLICK_MS) return
    gestureRef.current = { key, before: stagedRef.current, at: now }
  }, [])
  const restoreBeforeGesture = useCallback(
    (key: string) => {
      const current = gestureRef.current
      gestureRef.current = null
      if (current && current.key === key && Date.now() - current.at < DOUBLE_CLICK_MS * 2) {
        dispatch({ type: 'stage', staged: current.before })
      }
    },
    [dispatch]
  )

  const selectPending = useCallback(
    (id: string) => {
      beginGesture(`pending:${id}`)
      dispatch({ type: 'stage', staged: { kind: 'pending', id } })
    },
    [beginGesture, dispatch]
  )

  // Staging a history entry: NEXT previews it and nothing else changes. The
  // notice says "staged", never "shown" — nothing is on the display until the
  // server confirms the Show request.
  const selectHistory = useCallback(
    (reference: BibleReference) => {
      beginGesture(`history:${reference.id}`)
      dispatch({ type: 'stage', staged: { kind: 'history', reference } })
      showNotice('info', t('notice.stagedAgain', { ref: formatReference(reference) }))
    },
    [beginGesture, dispatch, showNotice, t]
  )

  const selectNext = useCallback(() => dispatch({ type: 'select_by', step: 1 }), [dispatch])
  const selectPrevious = useCallback(() => dispatch({ type: 'select_by', step: -1 }), [dispatch])
  const deselect = useCallback(() => dispatch({ type: 'stage', staged: null }), [dispatch])
  const showNext = useCallback(() => dispatch({ type: 'show_next' }), [dispatch])

  // Staging a lookup result: NEXT previews it and nothing else changes. The
  // notice says "staged", never "shown" — nothing is on the display until the
  // server confirms the Show request.
  const stageManualPassage = useCallback(
    (passage: Passage) => {
      dispatch({ type: 'stage', staged: { kind: 'manual', passage } })
      showNotice('info', t('notice.staged', { ref: formatReference(passageToReference(passage)) }))
    },
    [dispatch, showNotice, t]
  )

  // Enter in Find Scripture: the resolved coordinates go to the display
  // without staging anything first. This is the same `display_reference` action
  // `showNext` sends for a staged manual passage — coordinates only, resolved
  // by the server — so no new display path is introduced. The in-flight guard
  // is shared with Show: a confirmation or rejection settles it the same way.
  const showManualDirect = useCallback(
    (request: PassageQuery) => {
      dispatch({
        type: 'show_direct',
        request: {
          book: request.book,
          chapter: request.chapter,
          verse_start: request.verseStart,
          verse_end: request.verseEnd,
        },
      })
    },
    [dispatch]
  )

  // Double-click on a history entry: the same coordinates-only request Show sends
  // for a staged one, without staging first.
  const showHistory = useCallback(
    (reference: BibleReference) => {
      if (!isConnected) return
      restoreBeforeGesture(`history:${reference.id}`)
      showManualDirect({
        book: reference.book,
        chapter: reference.chapter,
        verseStart: reference.verse_start,
        verseEnd: reference.verse_end,
      })
    },
    [isConnected, restoreBeforeGesture, showManualDirect]
  )

  // Double-click on a queued detection: approved by ID exactly as Show approves
  // a staged one. Shares Show's in-flight guard, so a double-click is one request.
  const approvePending = useCallback(
    (id: string) => {
      if (!isConnected || showInFlight) return
      restoreBeforeGesture(`pending:${id}`)
      dispatch({ type: 'approve_pending', id })
    },
    [isConnected, showInFlight, restoreBeforeGesture, dispatch]
  )

  const plan = usePlan()
  const addToPlan = plan.add
  const planPassage = useCallback(
    (passage: Passage) => {
      const ref = formatReference(passageToReference(passage))
      if (addToPlan(passage)) showNotice('success', t('notice.planned', { ref }))
      else showNotice('info', t('notice.alreadyPlanned', { ref }))
    },
    [addToPlan, showNotice, t]
  )

  // Manual passage search: the server's canon, the server's text, and staging on
  // success only.
  // Search names books as the loaded Bible translation does, so it follows a switch.
  const search = useScriptureSearch(
    token,
    stageManualPassage,
    showManualDirect,
    bibleVersion,
    planPassage,
    // A plan row is clicked outside the search box, so its failure is a notice.
    message => showNotice('error', message)
  )
  const { stagePassage, cancelLookup } = search
  const stagePlanItem = useCallback(
    (item: PlanItem) => {
      beginGesture(`plan:${item.id}`)
      stagePassage(
        {
          book: item.book,
          chapter: item.chapter,
          verseStart: item.verse_start,
          verseEnd: item.verse_end,
        },
        true
      )
    },
    [beginGesture, stagePassage]
  )

  // The click that began a double-click has a lookup in flight; letting it land
  // after the display would stage a passage that is already on screen.
  const showPlanItem = useCallback(
    (item: PlanItem) => {
      if (!isConnected) return
      cancelLookup()
      restoreBeforeGesture(`plan:${item.id}`)
      showManualDirect({
        book: item.book,
        chapter: item.chapter,
        verseStart: item.verse_start,
        verseEnd: item.verse_end,
      })
    },
    [isConnected, cancelLookup, restoreBeforeGesture, showManualDirect]
  )

  // Staging is client-side and local to the console. A queued selection resolves
  // through the queue by ID, so it cannot drift from the list on screen; a manual
  // passage is a self-contained snapshot from the server's lookup.
  const selectedId = staged?.kind === 'pending' ? staged.id : null
  // History holds one entry per passage, so the staged one is found by its passage:
  // showing it again replaces the entry it was staged from with a new ID.
  const selectedHistoryId =
    staged?.kind === 'history'
      ? (history.find(entry => samePassage(entry, staged.reference))?.id ?? null)
      : null
  const nextReference =
    staged === null
      ? null
      : staged.kind === 'manual'
        ? passageToReference(staged.passage)
        : staged.kind === 'history'
          ? staged.reference
          : (pending.find(reference => reference.id === staged.id) ?? null)
  const nextSource: NextSource =
    staged === null
      ? null
      : staged.kind === 'manual'
        ? 'manual'
        : staged.kind === 'history'
          ? 'history'
          : 'detected'

  return {
    selectedId,
    selectedHistoryId,
    nextReference,
    nextSource,
    selectPending,
    selectHistory,
    selectNext,
    selectPrevious,
    deselect,
    showNext,
    showManualDirect,
    showHistory,
    approvePending,
    plan,
    search,
    stagePlanItem,
    showPlanItem,
  }
}
