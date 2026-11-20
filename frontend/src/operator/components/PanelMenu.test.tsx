import { act, fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { PanelMenu } from './PanelMenu'

/** The menu beside a button outside it, so a test can press or focus something else. */
function renderMenu() {
  render(
    <>
      <PanelMenu
        label="Queue options"
        placement="beside"
        items={[{ label: 'Clear queue', icon: <span />, onSelect: vi.fn() }]}
      />
      <button type="button">Outside</button>
    </>
  )
  return screen.getByRole('button', { name: 'Queue options' })
}

describe('PanelMenu', () => {
  it('closes on a pointer press outside it', () => {
    const trigger = renderMenu()
    fireEvent.click(trigger)
    expect(screen.getByRole('menu')).toBeInTheDocument()

    fireEvent.pointerDown(screen.getByRole('button', { name: 'Outside' }))

    expect(screen.queryByRole('menu')).not.toBeInTheDocument()
    expect(trigger).toHaveAttribute('aria-expanded', 'false')
  })

  it('closes when focus moves outside it', () => {
    renderMenu()
    fireEvent.click(screen.getByRole('button', { name: 'Queue options' }))
    // Opening the menu moves focus to its first item.
    expect(screen.getByRole('menuitem', { name: 'Clear queue' })).toHaveFocus()

    act(() => screen.getByRole('button', { name: 'Outside' }).focus())

    expect(screen.queryByRole('menu')).not.toBeInTheDocument()
  })
})
