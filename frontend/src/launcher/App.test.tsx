import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { App } from './App'
import { createDemoBridge } from './demoBridge'

describe('launcher App', () => {
  it('renders Simple and persists the switch to Advanced with its three tabs', async () => {
    const bridge = createDemoBridge()
    const setView = vi.spyOn(bridge, 'set_view')
    render(<App bridge={bridge} />)

    expect(await screen.findByRole('heading', { name: 'Start a session' })).toBeVisible()
    expect(screen.getByText('Preview mode')).toBeVisible()
    expect(screen.queryByRole('tab')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Advanced' }))

    expect(setView).toHaveBeenCalledWith('advanced')
    for (const name of ['Speech', 'Network', 'Launch']) {
      expect(screen.getByRole('tab', { name })).toBeVisible()
    }
  })

  it('uses the same elapsed status in both views and restores the idle microphone meter on stop', async () => {
    render(<App bridge={createDemoBridge()} />)
    expect(await screen.findByRole('meter', { name: 'Mic level' })).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: 'Start session' }))
    expect(screen.getByRole('meter', { name: 'In use by the session' })).toBeVisible()
    await screen.findByRole('button', { name: 'Stop session' }, { timeout: 3000 })
    const elapsed = /^Live · \d{2}:\d{2}:\d{2}$/
    expect(screen.getByText(elapsed)).toBeVisible()

    fireEvent.click(screen.getByRole('button', { name: 'Advanced' }))
    expect(screen.getByText(elapsed)).toBeVisible()
    expect(screen.getByRole('meter', { name: 'In use by the session' })).toBeVisible()

    fireEvent.click(screen.getByRole('button', { name: 'Stop session' }))
    expect(await screen.findByRole('meter', { name: 'Mic level' })).toBeVisible()
  })

  it('shows demo port validation in Advanced and focuses the invalid field', async () => {
    const bridge = createDemoBridge()
    render(<App bridge={bridge} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Advanced' }))
    fireEvent.click(screen.getByRole('tab', { name: 'Network' }))
    fireEvent.change(screen.getByRole('spinbutton', { name: 'Server port' }), {
      target: { value: '65536' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Simple' }))
    fireEvent.click(screen.getByRole('button', { name: 'Start session' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Enter a port from 1 to 65535.')
    const port = screen.getByRole('spinbutton', { name: 'Server port' })
    expect(port).toHaveAttribute('aria-invalid', 'true')
    await waitFor(() => expect(port).toHaveFocus())
  })
})
