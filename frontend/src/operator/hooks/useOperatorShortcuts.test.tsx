import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, render } from '@testing-library/react'
import { useOperatorShortcuts } from './useOperatorShortcuts'

interface HarnessProps {
  canShow?: boolean
  onNext?: () => void
  onPrevious?: () => void
  onShow?: () => void
  onDeselect?: () => void
  canClearDisplay?: boolean
  onClearDisplay?: () => void
  /** Focusable control to render beside nothing (`none` renders a plain div). */
  control?: 'none' | 'input' | 'button' | 'select'
  /** A queue-style card: a div with role="button" rather than a real button. */
  withCard?: boolean
  onCardSelect?: () => void
}

/** Minimal host that wires the hook up and optionally renders a control. */
function Harness({
  canShow = false,
  onNext = () => {},
  onPrevious = () => {},
  onShow = () => {},
  onDeselect = () => {},
  canClearDisplay = false,
  onClearDisplay = () => {},
  control = 'none',
  withCard = false,
  onCardSelect = () => {},
}: HarnessProps) {
  useOperatorShortcuts({
    canShow,
    canClearDisplay,
    onNext,
    onPrevious,
    onShow,
    onDeselect,
    onClearDisplay,
  })
  return (
    <div>
      {control === 'input' && <input aria-label="field" />}
      {control === 'button' && <button aria-label="action">Action</button>}
      {control === 'select' && (
        <select aria-label="book">
          <option>Genesis</option>
          <option>Exodus</option>
        </select>
      )}
      {withCard && <div role="button" tabIndex={0} aria-label="card" onKeyDown={onCardSelect} />}
    </div>
  )
}

function pressOn(target: EventTarget, key: string) {
  act(() => {
    target.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true }))
  })
}

afterEach(() => {
  vi.restoreAllMocks()
})

describe('useOperatorShortcuts', () => {
  it('maps arrow keys, Escape and Enter to their handlers', () => {
    const onNext = vi.fn()
    const onPrevious = vi.fn()
    const onShow = vi.fn()
    const onDeselect = vi.fn()
    render(
      <Harness
        canShow
        onNext={onNext}
        onPrevious={onPrevious}
        onShow={onShow}
        onDeselect={onDeselect}
      />
    )

    pressOn(window, 'ArrowDown')
    pressOn(window, 'ArrowUp')
    pressOn(window, 'Enter')
    pressOn(window, 'Escape')

    expect(onNext).toHaveBeenCalledTimes(1)
    expect(onPrevious).toHaveBeenCalledTimes(1)
    expect(onShow).toHaveBeenCalledTimes(1)
    expect(onDeselect).toHaveBeenCalledTimes(1)
  })

  it('leaves Enter alone while no visible row is highlighted', () => {
    const onShow = vi.fn()
    render(<Harness onShow={onShow} />)

    pressOn(window, 'Enter')

    expect(onShow).not.toHaveBeenCalled()
  })

  it('ignores shortcuts typed into a text field', () => {
    const onNext = vi.fn()
    const onShow = vi.fn()
    const { getByLabelText } = render(
      <Harness canShow control="input" onNext={onNext} onShow={onShow} />
    )
    const input = getByLabelText('field')

    pressOn(input, 'ArrowDown')
    pressOn(input, 'Enter')

    expect(onNext).not.toHaveBeenCalled()
    expect(onShow).not.toHaveBeenCalled()
  })

  it('leaves Enter to a focused button instead of showing the highlight', () => {
    const onShow = vi.fn()
    const { getByLabelText } = render(<Harness canShow control="button" onShow={onShow} />)

    pressOn(getByLabelText('action'), 'Enter')

    expect(onShow).not.toHaveBeenCalled()
  })

  it('leaves the arrow keys to a focused select', () => {
    const onNext = vi.fn()
    const { getByLabelText } = render(<Harness canShow control="select" onNext={onNext} />)

    pressOn(getByLabelText('book'), 'ArrowDown')

    expect(onNext).not.toHaveBeenCalled()
  })

  it('still answers Enter from a focused row, which focus highlighted', () => {
    const onShow = vi.fn()
    const { getByLabelText } = render(<Harness canShow withCard onShow={onShow} />)

    pressOn(getByLabelText('card'), 'Enter')

    expect(onShow).toHaveBeenCalledTimes(1)
  })

  it('shows once for a held Enter, ignoring the key repeats', () => {
    const onShow = vi.fn()
    render(<Harness canShow onShow={onShow} />)

    act(() => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', repeat: true }))
    })

    expect(onShow).toHaveBeenCalledTimes(1)
  })

  it.each(['ctrlKey', 'shiftKey', 'metaKey', 'altKey'])(
    'never shows the highlight for a modified Enter (%s)',
    modifier => {
      const onShow = vi.fn()
      render(<Harness canShow onShow={onShow} />)

      act(() => {
        window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', [modifier]: true }))
      })

      expect(onShow).not.toHaveBeenCalled()
    }
  )

  it('stops listening once unmounted', () => {
    const onNext = vi.fn()
    const { unmount } = render(<Harness onNext={onNext} />)

    unmount()
    pressOn(window, 'ArrowDown')

    expect(onNext).not.toHaveBeenCalled()
  })
})
