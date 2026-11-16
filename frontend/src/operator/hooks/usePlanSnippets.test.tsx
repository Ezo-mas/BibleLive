import { beforeEach, describe, expect, it, vi } from 'vitest'
import { act, renderHook, waitFor } from '@testing-library/react'
import { lookupPassage } from '../passageApi'
import type { Passage } from '../../shared/types'
import { usePlanSnippets } from './usePlanSnippets'
import type { PlanItem } from './usePlan'

vi.mock('../passageApi', () => ({ lookupPassage: vi.fn() }))

const lookup = vi.mocked(lookupPassage)
const item: PlanItem = {
  id: 'plan-1',
  book: 'John',
  book_label: null,
  chapter: 1,
  verse_start: 1,
  verse_end: 1,
  snippet: '',
}
const items = [item, { ...item, id: 'saved', snippet: 'Saved text' }]

function passage(text: string): Passage {
  return { ...item, text, verses: [{ verse: 1, text }] }
}

beforeEach(() => {
  lookup.mockReset()
})

describe('usePlanSnippets', () => {
  it('retries a failed lookup once and keeps the successful snippet', async () => {
    lookup.mockRejectedValueOnce(new Error('Unavailable')).mockResolvedValueOnce(passage('Text'))
    const { result } = renderHook(() => usePlanSnippets(items, null, true))

    await waitFor(() => expect(result.current).toEqual({ 'plan-1': 'Text' }))
    expect(lookup).toHaveBeenCalledTimes(2)
  })

  it('gives up after the retry fails, including on subsequent renders', async () => {
    lookup.mockRejectedValue(new Error('Unavailable'))
    const { result, rerender } = renderHook(() => usePlanSnippets([...items], null, true))

    await act(async () => {})
    rerender()
    await act(async () => {})
    expect(lookup).toHaveBeenCalledTimes(2)
    expect(result.current).toEqual({})
  })

  it('drops looked-up snippets and refetches only unsaved text on a version change', async () => {
    lookup.mockResolvedValueOnce(passage('English'))
    let resolveNew!: (value: Passage) => void
    lookup.mockImplementationOnce(
      () =>
        new Promise(resolve => {
          resolveNew = resolve
        })
    )
    const { result, rerender } = renderHook(
      ({ version }) => usePlanSnippets(items, null, true, version),
      { initialProps: { version: 'kjv' } }
    )
    await waitFor(() => expect(result.current).toEqual({ 'plan-1': 'English' }))

    rerender({ version: 'ltb' })
    expect(result.current).toEqual({})
    expect(lookup).toHaveBeenCalledTimes(2)
    await act(async () => resolveNew(passage('Lietuviškai')))
    expect(result.current).toEqual({ 'plan-1': 'Lietuviškai' })
    rerender({ version: 'ltb' })
    expect(lookup).toHaveBeenCalledTimes(2)
  })

  it('ignores an old response arriving after the new translation', async () => {
    let resolveOld!: (value: Passage) => void
    lookup.mockImplementationOnce(
      () =>
        new Promise(resolve => {
          resolveOld = resolve
        })
    )
    lookup.mockResolvedValueOnce(passage('Lietuviškai'))
    const { result, rerender } = renderHook(
      ({ version }) => usePlanSnippets(items, null, true, version),
      { initialProps: { version: 'kjv' } }
    )

    rerender({ version: 'ltb' })
    await waitFor(() => expect(result.current).toEqual({ 'plan-1': 'Lietuviškai' }))
    await act(async () => resolveOld(passage('English')))
    expect(result.current).toEqual({ 'plan-1': 'Lietuviškai' })
    expect(lookup).toHaveBeenCalledTimes(2)
  })

  it('does not refetch on the first version confirmation', async () => {
    lookup.mockResolvedValue(passage('Text'))
    const { result, rerender } = renderHook(
      ({ version }: { version: string | null }) => usePlanSnippets(items, null, true, version),
      { initialProps: { version: null as string | null } }
    )
    await waitFor(() => expect(result.current).toEqual({ 'plan-1': 'Text' }))

    rerender({ version: 'kjv' })
    expect(result.current).toEqual({ 'plan-1': 'Text' })
    expect(lookup).toHaveBeenCalledTimes(1)
  })
})
