import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, render } from '@testing-library/react'
import { useOperatorShortcuts } from './useOperatorShortcuts'

interface HarnessProps {
  hasSelection?: boolean
  onNext?: () => void
  onPrevious?: () => void
  onApprove?: () => void
  onDeselect?: () => void
  withInput?: boolean
}

/** Minimal host that wires the hook up and optionally renders a text field. */
function Harness({
  hasSelection = false,
  onNext = () => {},
  onPrevious = () => {},
  onApprove = () => {},
  onDeselect = () => {},
  withInput = false,
}: HarnessProps) {
  useOperatorShortcuts({ hasSelection, onNext, onPrevious, onApprove, onDeselect })
  return withInput ? <input aria-label="field" /> : <div />
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
      <Harness hasSelection withInput onNext={onNext} onApprove={onApprove} />
    )
    const input = getByLabelText('field')

    pressOn(input, 'ArrowDown')
    pressOn(input, 'Enter')

    expect(onNext).not.toHaveBeenCalled()
    expect(onApprove).not.toHaveBeenCalled()
  })

  it('stops listening once unmounted', () => {
    const onNext = vi.fn()
    const { unmount } = render(<Harness onNext={onNext} />)

    unmount()
    pressOn(window, 'ArrowDown')

    expect(onNext).not.toHaveBeenCalled()
  })
})
