import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, render } from '@testing-library/react'
import { useOperatorShortcuts } from './useOperatorShortcuts'

interface HarnessProps {
  hasSelection?: boolean
  onNext?: () => void
  onPrevious?: () => void
  onApprove?: () => void
  onDeselect?: () => void
  /** Focusable control to render beside nothing (`none` renders a plain div). */
  control?: 'none' | 'input' | 'button' | 'select'
  /** A queue-style card: a div with role="button" rather than a real button. */
  withCard?: boolean
  onCardSelect?: () => void
}

/** Minimal host that wires the hook up and optionally renders a control. */
function Harness({
  hasSelection = false,
  onNext = () => {},
  onPrevious = () => {},
  onApprove = () => {},
  onDeselect = () => {},
  control = 'none',
  withCard = false,
  onCardSelect = () => {},
}: HarnessProps) {
  useOperatorShortcuts({ hasSelection, onNext, onPrevious, onApprove, onDeselect })
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
    const onApprove = vi.fn()
    const onDeselect = vi.fn()
    render(
      <Harness
        hasSelection
        onNext={onNext}
        onPrevious={onPrevious}
        onApprove={onApprove}
        onDeselect={onDeselect}
      />
    )

    pressOn(window, 'ArrowDown')
    pressOn(window, 'ArrowUp')
    pressOn(window, 'Enter')
    pressOn(window, 'Escape')

    expect(onNext).toHaveBeenCalledTimes(1)
    expect(onPrevious).toHaveBeenCalledTimes(1)
    expect(onApprove).toHaveBeenCalledTimes(1)
    expect(onDeselect).toHaveBeenCalledTimes(1)
  })

  it('leaves Enter alone while nothing is selected', () => {
    const onApprove = vi.fn()
    render(<Harness onApprove={onApprove} />)

    pressOn(window, 'Enter')

    expect(onApprove).not.toHaveBeenCalled()
  })

  it('ignores shortcuts typed into a text field', () => {
    const onNext = vi.fn()
    const onApprove = vi.fn()
    const { getByLabelText } = render(
      <Harness hasSelection control="input" onNext={onNext} onApprove={onApprove} />
    )
    const input = getByLabelText('field')

    pressOn(input, 'ArrowDown')
    pressOn(input, 'Enter')

    expect(onNext).not.toHaveBeenCalled()
    expect(onApprove).not.toHaveBeenCalled()
  })

  it('leaves Enter to a focused button instead of approving', () => {
    const onApprove = vi.fn()
    const { getByLabelText } = render(
      <Harness hasSelection control="button" onApprove={onApprove} />
    )

    pressOn(getByLabelText('action'), 'Enter')

    expect(onApprove).not.toHaveBeenCalled()
  })

  it('leaves the arrow keys to a focused select', () => {
    const onNext = vi.fn()
    const { getByLabelText } = render(<Harness hasSelection control="select" onNext={onNext} />)

    pressOn(getByLabelText('book'), 'ArrowDown')

    expect(onNext).not.toHaveBeenCalled()
  })

  it('still answers Enter from a queue card, which is the console\u2019s own flow', () => {
    const onApprove = vi.fn()
    const { getByLabelText } = render(<Harness hasSelection withCard onApprove={onApprove} />)

    pressOn(getByLabelText('card'), 'Enter')

    expect(onApprove).toHaveBeenCalledTimes(1)
  })

  it('stops listening once unmounted', () => {
    const onNext = vi.fn()
    const { unmount } = render(<Harness onNext={onNext} />)

    unmount()
    pressOn(window, 'ArrowDown')

    expect(onNext).not.toHaveBeenCalled()
  })
})
