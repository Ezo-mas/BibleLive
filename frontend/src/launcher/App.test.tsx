import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { App } from './App'
import { createDemoBridge } from './demoBridge'

describe('launcher App', () => {
  it('renders Simple and persists the switch to Advanced with its three tabs', async () => {
    const bridge = createDemoBridge()
    const setView = vi.spyOn(bridge, 'set_view')
    const fitWindow = vi.spyOn(bridge, 'fit_window')
    const contentHeight = vi.spyOn(HTMLElement.prototype, 'scrollHeight', 'get')
    contentHeight.mockImplementation(function (this: HTMLElement) {
      return this.dataset.mode === 'simple' ? 520 : 750
    })
    render(<App bridge={bridge} />)

    try {
      expect(await screen.findByRole('heading', { name: 'Start a session' })).toBeVisible()
      expect(screen.getByText('Preview mode')).toBeVisible()
      expect(screen.queryByRole('tab')).not.toBeInTheDocument()
      await waitFor(() => expect(fitWindow).toHaveBeenCalledWith(520, window.innerHeight))
      fireEvent.click(screen.getByRole('button', { name: 'Advanced' }))

      expect(setView).toHaveBeenCalledWith('advanced')
      for (const name of ['Speech', 'Network', 'Launch']) {
        expect(screen.getByRole('tab', { name })).toBeVisible()
      }
      await waitFor(() => expect(fitWindow).toHaveBeenLastCalledWith(750, window.innerHeight))
      fireEvent.resize(window)
      fireEvent.click(screen.getByRole('tab', { name: 'Network' }))
      fireEvent.click(screen.getByRole('button', { name: 'Simple' }))
      await waitFor(() => expect(fitWindow).toHaveBeenLastCalledWith(520, window.innerHeight))
      expect(fitWindow).toHaveBeenCalledTimes(3)
    } finally {
      contentHeight.mockRestore()
    }
  })

  it('drives both mic visuals from microphone levels and clears them on meter error', async () => {
    const bridge = createDemoBridge()
    const poll = bridge.poll
    const meterError = 'Could not open the microphone. Check the selected device and try again.'
    let meterOn = true
    vi.spyOn(bridge, 'poll').mockImplementation(async () => ({
      ...(await poll()),
      meter: meterOn ? 'on' : 'error',
      meter_error: meterOn ? null : meterError,
      levels: meterOn ? [0.8] : [],
    }))
    const { container } = render(<App bridge={bridge} />)
    const meter = await screen.findByRole('meter', { name: 'Mic level' })
    const readout = container.querySelector('.bl-meter-readout')
    const status = screen.getByRole('status')
    const peak = meter.querySelector('.bl-meter-peak')
    const bars = Array.from(container.querySelectorAll<HTMLElement>('.bl-wave span'))
    const centre = bars[bars.length / 2]

    expect(meter).toHaveAttribute('aria-valuemin', '-60')
    expect(meter).toHaveAttribute('aria-valuemax', '0')
    await waitFor(() => {
      const db = Number(meter.getAttribute('aria-valuenow'))
      expect(db).toBeGreaterThanOrEqual(-20)
      expect(meter).toHaveAttribute('aria-valuetext', `${db} decibels`)
      expect(readout).toHaveTextContent(/^-\d+ dB$/)
      expect(readout).toHaveAttribute('data-zone', 'yellow')
      expect(peak).toHaveAttribute('data-zone', 'yellow')
      expect(Number(centre.style.getPropertyValue('--wave-level'))).toBeGreaterThan(0.5)
    })

    meterOn = false
    await waitFor(() => {
      expect(meter).toHaveAttribute('aria-valuenow', '-60')
      expect(meter).toHaveAttribute('aria-valuetext', 'Silence')
      expect(readout).toHaveTextContent('-∞')
      expect(peak).toHaveStyle({ opacity: '0' })
      expect(bars.every(bar => bar.style.getPropertyValue('--wave-level') === '0')).toBe(true)
      const error = within(status).getByText(meterError)
      expect(error).toHaveAttribute('title', meterError)
      expect(status.closest('.bl-meter-label')).toBe(readout?.parentElement)
      expect(within(status).queryByText('Mic level')).not.toBeInTheDocument()
    })
  })

  it('replaces the meter caption with a no-sound warning and keeps the readout in the row', async () => {
    vi.useFakeTimers()
    const app = render(<App bridge={createDemoBridge()} />)
    try {
      await act(() => vi.advanceTimersByTimeAsync(100))
      const status = screen.getByRole('status')
      const meter = screen.getByRole('meter', { name: 'Mic level' })
      const microphone = screen.getByRole('combobox', { name: 'Microphone' })
      expect(status).toHaveTextContent('Mic level')

      fireEvent.change(microphone, { target: { value: 'built-in' } })
      await act(() => vi.advanceTimersByTimeAsync(5_500))
      const warning = within(status).getByText('No sound from this microphone')
      const details = 'Check that it is plugged in and not muted, or choose another.'
      expect(warning).toBeVisible()
      expect(warning).toHaveAttribute('title', details)
      expect(meter).toHaveAccessibleDescription(details)
      expect(status.closest('.bl-meter-label')?.querySelector('.bl-meter-readout')).toBeVisible()
      expect(within(status).queryByText('Mic level')).not.toBeInTheDocument()

      fireEvent.change(microphone, { target: { value: 'usb' } })
      await act(() => vi.advanceTimersByTimeAsync(100))
      expect(status).toHaveTextContent('Mic level')
      expect(meter).not.toHaveAttribute('aria-describedby')
    } finally {
      app.unmount()
      vi.useRealTimers()
    }
  })

  it('uses the same elapsed status in both views and restores the idle microphone meter on stop', async () => {
    render(<App bridge={createDemoBridge()} />)
    expect(await screen.findByRole('meter', { name: 'Mic level' })).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: 'Start session' }))
    expect(screen.getByRole('meter', { name: 'In use by the session' })).toBeVisible()
    const elapsed = /^Live · \d{2}:\d{2}:\d{2}$/
    expect(await screen.findByText(elapsed, {}, { timeout: 3000 })).toBeVisible()

    fireEvent.click(screen.getByRole('button', { name: 'Advanced' }))
    expect(screen.getByText(elapsed)).toBeVisible()
    expect(screen.getByRole('meter', { name: 'In use by the session' })).toBeVisible()

    fireEvent.click(screen.getByRole('button', { name: 'Stop session' }))
    expect(await screen.findByRole('meter', { name: 'Mic level' })).toBeVisible()
  })

  it('can stop while the session is starting and disables Stop while it is stopping', async () => {
    const bridge = createDemoBridge()
    const start = bridge.start
    let acknowledge = () => {}
    vi.spyOn(bridge, 'start').mockImplementation(async form => {
      const result = await start(form)
      await new Promise<void>(resolve => {
        acknowledge = resolve
      })
      return result
    })
    const stop = vi.spyOn(bridge, 'stop')
    render(<App bridge={bridge} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Start session' }))

    await waitFor(() => expect(screen.getByRole('button', { name: 'Stop session' })).toBeEnabled())
    expect(screen.getByRole('heading', { name: 'Starting a session' })).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: 'Stop session' }))
    expect(stop).toHaveBeenCalledOnce()
    expect(screen.getByRole('button', { name: 'Stopping…' })).toBeDisabled()
    await act(async () => acknowledge())
    expect(await screen.findByRole('meter', { name: 'Mic level' })).toBeVisible()
  })

  it('shows a bridge error after 15 seconds and still accepts a late connection', async () => {
    vi.stubEnv('DEV', false)
    vi.useFakeTimers()
    const app = render(<App />)
    try {
      await act(() => vi.advanceTimersByTimeAsync(15_000))
      expect(screen.getByRole('status')).toHaveTextContent(
        'Could not communicate with the launcher.'
      )

      window.pywebview = { api: createDemoBridge() }
      await act(async () => {
        window.dispatchEvent(new Event('pywebviewready'))
      })
      expect(screen.getByRole('heading', { name: 'Start a session' })).toBeVisible()
    } finally {
      app.unmount()
      delete window.pywebview
      vi.useRealTimers()
      vi.unstubAllEnvs()
    }
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
