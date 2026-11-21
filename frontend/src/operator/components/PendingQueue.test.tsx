import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { I18nProvider } from '../../shared/I18nProvider'
import { LANGUAGE_STORAGE_KEY } from '../../shared/i18n'
import type { BibleReference } from '../../shared/types'
import { PendingQueue } from './PendingQueue'

const ref: BibleReference = {
  id: 'pending-1',
  book: 'John',
  chapter: 3,
  verse_start: 16,
  verse_end: null,
  confidence: 'high',
  inferred: false,
  text: 'Verse text',
  verses: [{ verse: 16, text: 'Verse text' }],
  created_at: '2026-01-01T00:00:00Z',
}

beforeEach(() => window.localStorage.clear())

describe('Pending row Remove', () => {
  it.each([
    { language: 'en', theme: 'light', label: 'Remove John 3:16 from the queue' },
    { language: 'en', theme: 'dark', label: 'Remove John 3:16 from the queue' },
    { language: 'lt', theme: 'light', label: 'Pašalinti John 3:16 iš eilės' },
    { language: 'lt', theme: 'dark', label: 'Pašalinti John 3:16 iš eilės' },
  ])('removes just its row in $language / $theme', ({ language, theme, label }) => {
    window.localStorage.setItem(LANGUAGE_STORAGE_KEY, language)
    document.documentElement.setAttribute('data-theme', theme)
    const onRemove = vi.fn()
    const onShow = vi.fn()
    const onSelect = vi.fn()
    render(
      <I18nProvider>
        <PendingQueue
          pending={[ref]}
          selectedId={null}
          onSelect={onSelect}
          onShow={onShow}
          onRemove={onRemove}
        />
      </I18nProvider>
    )
    const remove = screen.getByRole('button', { name: label })
    // A neutral icon-only square, named and titled with its reference.
    expect(remove).toHaveClass('btn', 'btn-ghost', 'btn-sm', 'btn-square')
    expect(remove).toHaveAttribute('title', label)
    expect(remove.textContent).toBe('')
    remove.focus()
    expect(remove).toHaveFocus()
    fireEvent.click(remove)
    expect(onRemove).toHaveBeenCalledExactlyOnceWith(ref.id)
    expect(onShow).not.toHaveBeenCalled()
    expect(onSelect).not.toHaveBeenCalled()
  })

  it('a double-click shows the row without selecting a word; a single press still can', () => {
    const onShow = vi.fn()
    render(
      <I18nProvider>
        <PendingQueue
          pending={[ref]}
          selectedId={null}
          onSelect={vi.fn()}
          onShow={onShow}
          onRemove={vi.fn()}
        />
      </I18nProvider>
    )
    const text = screen.getAllByText('Verse text')[0]
    // fireEvent returns false when the handler called preventDefault.
    expect(fireEvent.mouseDown(text, { detail: 1 })).toBe(true)
    expect(fireEvent.mouseDown(text, { detail: 2 })).toBe(false)
    fireEvent.doubleClick(text)
    expect(onShow).toHaveBeenCalledExactlyOnceWith(ref.id)
  })

  it('disables Remove while disconnected', () => {
    const onRemove = vi.fn()
    render(
      <PendingQueue
        pending={[ref]}
        selectedId={null}
        onSelect={vi.fn()}
        onShow={vi.fn()}
        onRemove={onRemove}
        canShow={false}
      />
    )
    const remove = screen.getByRole('button', { name: /^Remove / })
    expect(remove).toBeDisabled()
    fireEvent.click(remove)
    expect(onRemove).not.toHaveBeenCalled()
  })

  it('disables every Remove while a discard is in flight', () => {
    const onRemove = vi.fn()
    const { rerender } = render(
      <PendingQueue
        pending={[ref, { ...ref, id: 'pending-2' }]}
        selectedId={null}
        onSelect={vi.fn()}
        onShow={vi.fn()}
        onRemove={onRemove}
        discardInFlight
      />
    )
    for (const button of screen.getAllByRole('button', { name: /^Remove / })) {
      expect(button).toBeDisabled()
      fireEvent.click(button)
    }
    expect(onRemove).not.toHaveBeenCalled()
    rerender(
      <PendingQueue
        pending={[ref, { ...ref, id: 'pending-2' }]}
        selectedId={null}
        onSelect={vi.fn()}
        onShow={vi.fn()}
        onRemove={onRemove}
        discardInFlight={false}
      />
    )
    fireEvent.click(screen.getAllByRole('button', { name: /^Remove / })[1])
    expect(onRemove).toHaveBeenCalledExactlyOnceWith('pending-2')
  })
})

describe('detection confidence on a Pending row', () => {
  function renderQueue(pending: BibleReference[], selectedId: string | null = null) {
    return render(
      <PendingQueue
        pending={pending}
        selectedId={selectedId}
        onSelect={vi.fn()}
        onShow={vi.fn()}
        onRemove={vi.fn()}
      />
    )
  }

  /** The row (`.ref-item`) holding the reference's title. */
  function rowOf(title: string) {
    return screen.getByRole('heading', { name: title }).closest('.ref-item') as HTMLElement
  }

  const low = { ...ref, id: 'low', book: 'Acts', confidence: 'low' as const }
  const medium = { ...ref, id: 'medium', book: 'Romans', confidence: 'medium' as const }
  const high = { ...ref, id: 'high', book: 'Luke', confidence: 'high' as const }

  it('shows no confidence word on the card', () => {
    renderQueue([low, medium, high])

    for (const word of ['High', 'Medium', 'Low']) {
      expect(screen.queryByText(word)).not.toBeInTheDocument()
    }
  })

  it('gives a Low row the dashed edge, and Medium and High the same normal edge', () => {
    renderQueue([low, medium, high])

    expect(rowOf('Acts 3:16')).toHaveAttribute('data-confidence', 'low')
    expect(rowOf('Romans 3:16')).toHaveAttribute('data-confidence', 'medium')
    expect(rowOf('Luke 3:16')).toHaveAttribute('data-confidence', 'high')
    // Nothing but the highlight marks a row with the accent.
    for (const title of ['Acts 3:16', 'Romans 3:16', 'Luke 3:16']) {
      expect(rowOf(title)).not.toHaveClass('is-selected')
    }
  })

  it('keeps the confidence for screen readers and as a tooltip', () => {
    renderQueue([low, high])

    expect(
      screen.getByRole('button', { name: 'Acts 3:16 — highlight; Enter shows it' })
    ).toHaveAccessibleDescription('Detection confidence: Low')
    expect(rowOf('Acts 3:16')).toHaveAttribute('title', 'Detection confidence: Low')
    expect(rowOf('Luke 3:16')).toHaveAttribute('title', 'Detection confidence: High')
  })

  it('describes a row by its badges as well as its confidence', () => {
    renderQueue([
      { ...low, inferred: true },
      { ...ref, id: 'manual', book: 'Mark', confidence: null, source: 'manual' },
    ])

    expect(
      screen.getByRole('button', { name: 'Acts 3:16 — highlight; Enter shows it' })
    ).toHaveAccessibleDescription(/Context.*Detection confidence: Low/)
    expect(
      screen.getByRole('button', { name: 'Mark 3:16 — highlight; Enter shows it' })
    ).toHaveAccessibleDescription(/Search/)
  })

  it('a highlighted Low row is still the highlighted row', () => {
    renderQueue([low, high], 'low')

    expect(rowOf('Acts 3:16')).toHaveClass('is-selected')
    expect(rowOf('Acts 3:16')).toHaveAttribute('data-confidence', 'low')
    expect(
      screen.getByRole('button', { name: 'Acts 3:16 — highlight; Enter shows it' })
    ).toHaveAttribute('aria-pressed', 'true')
  })

  it('keeps the Context badge, and a manual row has no confidence', () => {
    renderQueue([
      { ...low, inferred: true },
      { ...ref, id: 'manual', book: 'Mark', confidence: null, source: 'manual' },
    ])

    expect(screen.getByText('Context')).toBeInTheDocument()
    expect(rowOf('Mark 3:16')).not.toHaveAttribute('data-confidence')
    expect(rowOf('Mark 3:16')).not.toHaveAttribute('title')
  })
})
