import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { SegmentedOption } from './SegmentedOption'

describe('SegmentedOption', () => {
  it('is a button whose label carries a hidden copy of itself for the bold width', () => {
    render(
      <div className="segmented" role="group" aria-label="Verses">
        <SegmentedOption aria-pressed={true}>10</SegmentedOption>
        <SegmentedOption aria-pressed={false}>All</SegmentedOption>
      </div>
    )

    const selected = screen.getByRole('button', { name: '10' })
    expect(selected).toHaveClass('segmented-option')
    expect(selected).toHaveAttribute('type', 'button')
    expect(selected).toHaveAttribute('aria-pressed', 'true')
    // The copy is CSS-only (`data-label`), so the accessible name stays the label.
    expect(selected.querySelector('.segmented-label')).toHaveAttribute('data-label', '10')
    expect(screen.getByRole('button', { name: 'All' })).toHaveAttribute('aria-pressed', 'false')
  })

  it('draws an icon before the label and passes the rest to the button', () => {
    render(
      <SegmentedOption icon={<svg data-testid="icon" />} role="radio" aria-checked={true}>
        Light
      </SegmentedOption>
    )

    const option = screen.getByRole('radio', { name: 'Light' })
    expect(option).toHaveAttribute('aria-checked', 'true')
    expect(option.firstElementChild).toBe(screen.getByTestId('icon'))
  })
})
