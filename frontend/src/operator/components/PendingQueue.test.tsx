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
    { language: 'en', theme: 'light', label: 'Remove' },
    { language: 'en', theme: 'dark', label: 'Remove' },
    { language: 'lt', theme: 'light', label: 'Pašalinti' },
    { language: 'lt', theme: 'dark', label: 'Pašalinti' },
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
    expect(remove).toHaveClass('btn', 'btn-danger', 'btn-sm')
    expect(remove).toHaveAccessibleDescription('John 3:16')
    remove.focus()
    expect(remove).toHaveFocus()
    fireEvent.click(remove)
    expect(onRemove).toHaveBeenCalledExactlyOnceWith(ref.id)
    expect(onShow).not.toHaveBeenCalled()
    expect(onSelect).not.toHaveBeenCalled()
  })

  it('disables Remove while disconnected, like Show', () => {
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
    const remove = screen.getByRole('button', { name: 'Remove' })
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
    for (const button of screen.getAllByRole('button', { name: 'Remove' })) {
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
    fireEvent.click(screen.getAllByRole('button', { name: 'Remove' })[1])
    expect(onRemove).toHaveBeenCalledExactlyOnceWith('pending-2')
  })
})
