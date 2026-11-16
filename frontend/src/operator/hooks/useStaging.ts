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
  // A double-click is click, click, double-click. Restore what Up Next held
  // before those clicks, so showing a row directly does not replace the staging.
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

  const stageManualPassage = useCallback(
    (passage: Passage) => {
      dispatch({ type: 'stage', staged: { kind: 'manual', passage } })
      showNotice('info', t('notice.staged', { ref: formatReference(passageToReference(passage)) }))
    },
    [dispatch, showNotice, t]
  )

  // Enter in Find Scripture sends coordinates without changing Up Next.
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

  const search = useScriptureSearch(
    token,
    stageManualPassage,
    showManualDirect,
    bibleVersion,
    planPassage,
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

  // Cancel the first click's lookup before showing, so it cannot stage afterward.
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

  const selectedId = staged?.kind === 'pending' ? staged.id : null
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
