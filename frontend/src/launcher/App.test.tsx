import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { App } from './App'
import type { PortCheck, SpeechCheck, State } from './bridge'
import { createDemoBridge } from './demoBridge'

describe('launcher App', () => {
  const speechTarget = 'Aim for speech peaks between -24 and -12 dB'

  it('shows downloaded models and fallback for the latest Speech settings', async () => {
    const bridge = createDemoBridge()
    const init = bridge.init
    vi.spyOn(bridge, 'init').mockImplementation(async () => {
      const initial = await init()
      return { ...initial, form: { ...initial.form, language: 'lt' } }
    })
    let finishFirst!: (result: SpeechCheck) => void
    const latest: SpeechCheck = {
      wanted: 'kmynas+canary',
      runs: 'kmynas',
      models: [
        { name: 'kmynas', state: 'old' },
        { name: 'canary', state: 'missing' },
      ],
    }
    const check = vi
      .spyOn(bridge, 'check_speech')
      .mockImplementationOnce(() => new Promise(resolve => (finishFirst = resolve)))
      .mockResolvedValue(latest)
    render(<App bridge={bridge} />)

    fireEvent.click(await screen.findByRole('button', { name: 'Advanced' }))
    expect(screen.getByText('Checking downloaded models…')).toBeVisible()
    expect(screen.queryByText('Canary — not downloaded')).not.toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Processing device'), { target: { value: 'cpu' } })
    expect(await screen.findByText('Canary — not downloaded')).toHaveClass('bl-warning')
    expect(screen.getByText('Kmynas — older download')).toBeVisible()
    expect(screen.getByText('Canary is missing. Kmynas alone will run.')).toBeVisible()
    expect(screen.getByText(/--download-model --speech-engine kmynas\+canary/)).toBeVisible()
    expect(check.mock.calls).toEqual([
      ['auto', 'auto'],
      ['auto', 'cpu'],
    ])
    await act(async () =>
      finishFirst({
        wanted: 'kmynas',
        runs: 'kmynas',
        models: [{ name: 'kmynas', state: 'ok' }],
      })
    )
    expect(screen.getByText('Canary — not downloaded')).toBeVisible()
    fireEvent.change(screen.getByLabelText('Lithuanian engine'), { target: { value: 'whisper' } })
    expect(screen.queryByText('Canary — not downloaded')).not.toBeInTheDocument()
  })

  it('runs the self-test on demand and disables its button while testing or starting a session', async () => {
    const bridge = createDemoBridge()
    const run = vi.spyOn(bridge, 'run_self_test')
    render(<App bridge={bridge} />)

    fireEvent.click(await screen.findByRole('button', { name: 'Advanced' }))
    fireEvent.click(screen.getByText('Troubleshooting'))
    const button = screen.getByRole('button', { name: 'Test speech models' })
    expect(button).toBeEnabled()
    expect(run).not.toHaveBeenCalled()
    fireEvent.click(button)
    expect(button).toBeDisabled()
    expect(screen.getByText('Testing speech models…')).toBeVisible()
    expect(run).toHaveBeenCalledWith(expect.objectContaining({ speech_engine: 'auto' }))
    expect(
      await screen.findByText('Speech models passed the demo test.', { selector: '[role=status]' })
    ).toBeVisible()
    expect(button).toBeEnabled()
    fireEvent.click(screen.getByRole('button', { name: 'Start session' }))
    expect(button).toBeDisabled()
  })

  it('identifies screens through the bridge when monitors are listed', async () => {
    const bridge = createDemoBridge()
    const identify = vi.spyOn(bridge, 'identify_screens')
    render(<App bridge={bridge} />)

    fireEvent.click(await screen.findByRole('button', { name: 'Identify screens' }))
    expect(identify).toHaveBeenCalledOnce()
  })

  it('checks on launch and Network, debounces port edits, and ignores stale replies', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'performance'] })
    try {
      const bridge = createDemoBridge()
      const pending = new Map<string, (value: PortCheck) => void>()
      const check = vi
        .spyOn(bridge, 'check_port')
        .mockImplementation(port => new Promise(resolve => pending.set(port, resolve)))
      const app = render(<App bridge={bridge} />)
      await act(async () => {})
      expect(check.mock.calls).toEqual([['8000']])
      fireEvent.click(screen.getByRole('button', { name: 'Advanced' }))
      fireEvent.click(screen.getByRole('tab', { name: 'Network' }))
      expect(check.mock.calls).toEqual([['8000'], ['8000']])

      const port = screen.getByRole('spinbutton', { name: 'Server port' })
      fireEvent.change(port, { target: { value: '8001' } })
      await act(() => vi.advanceTimersByTimeAsync(200))
      fireEvent.change(port, { target: { value: '8002' } })
      await act(() => vi.advanceTimersByTimeAsync(399))
      expect(check).toHaveBeenCalledTimes(2)
      await act(() => vi.advanceTimersByTimeAsync(1))
      expect(check).toHaveBeenLastCalledWith('8002')

      const busy = { free: false, program: 'obs64.exe', pid: 1234, bibleLive: false }
      await act(async () => pending.get('8002')?.(busy))
      const warning = 'Port 8002 is in use by obs64.exe.'
      expect(screen.getAllByText(warning)).toHaveLength(2)
      expect(
        screen.getAllByText(warning).filter(node => node.closest('.bl-status-strip'))
      ).toHaveLength(1)
      expect(port).toHaveAccessibleDescription(warning)
      expect(screen.getByRole('button', { name: 'Start session' })).toBeEnabled()
      await act(async () => pending.get('8000')?.(busy))
      expect(screen.getAllByText(warning)).toHaveLength(2)
      expect(screen.queryByText('Port 8000 is in use by obs64.exe.')).not.toBeInTheDocument()
      fireEvent.focus(window)
      expect(check).toHaveBeenCalledTimes(3)
      await act(() => vi.advanceTimersByTimeAsync(3000))
      fireEvent.focus(window)
      fireEvent.focus(window)
      expect(check.mock.calls).toEqual([['8000'], ['8000'], ['8002'], ['8002']])
      app.unmount()
    } finally {
      vi.useRealTimers()
    }
  })

  it('shows only the Start-time error when a subsequent port check confirms the conflict', async () => {
    const bridge = createDemoBridge()
    const warning = 'Port 8000 is in use by obs64.exe.'
    vi.spyOn(bridge, 'check_port').mockResolvedValue({
      free: false,
      program: 'obs64.exe',
      pid: 1234,
      bibleLive: false,
    })
    const poll = bridge.poll
    let failed = false
    vi.spyOn(bridge, 'start').mockImplementation(async () => {
      failed = true
      return { ok: true }
    })
    vi.spyOn(bridge, 'poll').mockImplementation(async () => {
      const result = await poll()
      return failed
        ? { ...result, state: { ...result.state, error: { text: warning, port: 8000 } } }
        : result
    })
    render(<App bridge={bridge} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Advanced' }))
    fireEvent.click(screen.getByRole('tab', { name: 'Network' }))
    await waitFor(() => expect(screen.getAllByText(warning)).toHaveLength(2))
    fireEvent.click(screen.getByRole('button', { name: 'Start session' }))
    const changePort = await screen.findByRole('button', { name: 'Change port' })
    expect(screen.queryByRole('button', { name: 'Show details' })).not.toBeInTheDocument()
    await waitFor(() => expect(screen.getByRole('button', { name: 'Start session' })).toBeEnabled())
    expect(screen.getAllByText(warning)).toHaveLength(1)
    const port = screen.getByRole('spinbutton', { name: 'Server port' })
    expect(port).not.toHaveAccessibleDescription(warning)

    // The hand-off goes to the field that fixes it, from any tab.
    fireEvent.click(screen.getByRole('tab', { name: 'Speech' }))
    fireEvent.click(changePort)
    expect(screen.getByRole('tab', { name: 'Network' })).toHaveAttribute('aria-selected', 'true')
    await waitFor(() =>
      expect(screen.getByRole('spinbutton', { name: 'Server port' })).toHaveFocus()
    )
  })

  it('keeps Show details on the console for errors that are not a port conflict', async () => {
    const bridge = createDemoBridge()
    const poll = bridge.poll
    vi.spyOn(bridge, 'poll').mockImplementation(async () => {
      const result = await poll()
      return { ...result, state: { ...result.state, error: { text: 'Server failed to start' } } }
    })
    render(<App bridge={bridge} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Show details' }))
    expect(screen.queryByRole('button', { name: 'Change port' })).not.toBeInTheDocument()
    await waitFor(() => expect(screen.getByRole('region', { name: 'Session log' })).toHaveFocus())
  })

  it('collects diagnostics and shows the polled progress, filename and error', async () => {
    const bridge = createDemoBridge()
    const poll = bridge.poll
    let diagnostics: State['diagnostics'] = { status: 'idle', file: null, text: null }
    const collect = vi.spyOn(bridge, 'collect_diagnostics').mockImplementation(async () => {
      if (diagnostics.status === 'running') return { ok: false, error: 'Already collecting' }
      diagnostics = { status: 'running', file: null, text: null }
      return { ok: true }
    })
    vi.spyOn(bridge, 'poll').mockImplementation(async () => {
      const result = await poll()
      return { ...result, state: { ...result.state, diagnostics } }
    })
    render(<App bridge={bridge} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Advanced' }))
    const button = screen.getByRole('button', { name: 'Diagnostics' })
    const hint =
      'Collect logs, recent service transcripts, and settings in a ZIP. Audio and the access token are excluded.'
    expect(button).toHaveAttribute('title', hint)
    fireEvent.click(button)
    fireEvent.click(button)
    expect(collect).toHaveBeenCalledTimes(2)
    await waitFor(() => expect(button).toBeDisabled())
    expect(button).toHaveAttribute('aria-busy', 'true')
    expect(button).toHaveAccessibleName('Diagnostics')
    expect(screen.queryByText('Could not communicate with the launcher.')).not.toBeInTheDocument()
    const file = 'BibleLive-diagnostics-20261124-120000.zip'
    diagnostics = { status: 'done', file, text: null }
    await waitFor(() => expect(button).toHaveAttribute('title', `${hint}\n${file}`))
    // Only announced to screen readers; the header shows no result text (no layout jump).
    expect(screen.getByText(file)).toHaveClass('bl-sr-only')
    expect(button).toBeEnabled()
    fireEvent.click(button)
    diagnostics = { status: 'error', file: null, text: 'Could not create the diagnostics bundle.' }
    await waitFor(() => expect(button).toHaveAttribute('title', `${hint}\n${diagnostics.text}`))
    expect(button).toBeEnabled()
  })

  it('restores the Advanced tab and console filters and saves changes to them', async () => {
    const bridge = createDemoBridge()
    const init = bridge.init.bind(bridge)
    bridge.init = async () => ({
      ...(await init()),
      view: 'advanced',
      tab: 'launch',
      console: { source: 'server', level: 'WARNING' },
    })
    const setTab = vi.spyOn(bridge, 'set_advanced_tab')
    const setFilters = vi.spyOn(bridge, 'set_console_filters')
    render(<App bridge={bridge} />)

    expect(await screen.findByRole('tab', { name: 'Launch' })).toHaveAttribute(
      'aria-selected',
      'true'
    )
    expect(screen.getByRole('button', { name: 'Server' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByLabelText('Minimum level')).toHaveValue('WARNING')
    expect(setTab).not.toHaveBeenCalled()
    expect(setFilters).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('tab', { name: 'Network' }))
    expect(setTab).toHaveBeenCalledWith('network')
    fireEvent.click(screen.getByRole('button', { name: 'Audio' }))
    expect(setFilters).toHaveBeenLastCalledWith('audio', 'WARNING')
    fireEvent.change(screen.getByLabelText('Minimum level'), { target: { value: 'ERROR' } })
    expect(setFilters).toHaveBeenLastCalledWith('audio', 'ERROR')
  })

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
    expect(meter).toHaveAccessibleDescription(speechTarget)
    expect(meter).toHaveAttribute('title', speechTarget)
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

  it('moves the live wave with the pipeline levels, flat without them, the meter at rest', async () => {
    const bridge = createDemoBridge()
    const poll = bridge.poll
    let pipeline: number[] = []
    vi.spyOn(bridge, 'poll').mockImplementation(async () => {
      const result = await poll()
      return { ...result, levels: result.state.session === 'live' ? pipeline : [] }
    })
    const { container } = render(<App bridge={bridge} />)
    await screen.findByRole('meter', { name: 'Mic level' })
    const root = container.querySelector('#bl-launcher')
    const bars = Array.from(container.querySelectorAll<HTMLElement>('.bl-wave span'))
    const centre = bars[bars.length / 2]
    const flat = () => bars.every(bar => bar.style.getPropertyValue('--wave-level') === '0')
    expect(bars.every(bar => bar.style.getPropertyValue('--wave-live') === '')).toBe(true)

    fireEvent.click(screen.getByRole('button', { name: 'Start session' }))
    await waitFor(() => expect(root).toHaveAttribute('data-status', 'live'), { timeout: 3000 })
    const meter = screen.getByRole('meter', { name: 'In use by the session' })
    const fill = meter.querySelector<HTMLElement>('.bl-meter-fill')
    // Live, but the pipeline has sent nothing yet: a still, flat wave.
    await new Promise(resolve => window.setTimeout(resolve, 300))
    expect(flat()).toBe(true)

    pipeline = [0.8]
    await waitFor(() =>
      expect(Number(centre.style.getPropertyValue('--wave-level'))).toBeGreaterThan(0.5)
    )
    // The meter says the session holds the microphone: it does not move.
    expect(meter).toHaveAttribute('aria-valuenow', '-60')
    expect(fill?.style.clipPath).toBe('inset(0 100% 0 0)')

    fireEvent.click(screen.getByRole('button', { name: 'Stop session' }))
    await waitFor(() => expect(flat()).toBe(true))
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
      expect(meter).toHaveAccessibleDescription(`${details} ${speechTarget}`)
      expect(status.closest('.bl-meter-label')?.querySelector('.bl-meter-readout')).toBeVisible()
      expect(within(status).queryByText('Mic level')).not.toBeInTheDocument()

      fireEvent.change(microphone, { target: { value: 'usb' } })
      await act(() => vi.advanceTimersByTimeAsync(100))
      expect(status).toHaveTextContent('Mic level')
      expect(meter).toHaveAccessibleDescription(speechTarget)
    } finally {
      app.unmount()
      vi.useRealTimers()
    }
  })

  it('decays the held peak after silence and clears it on a mic change even with a poll in flight', async () => {
    vi.useFakeTimers({
      toFake: [
        'Date',
        'performance',
        'setTimeout',
        'clearTimeout',
        'requestAnimationFrame',
        'cancelAnimationFrame',
      ],
    })
    const bridge = createDemoBridge()
    const poll = bridge.poll
    let level = 0.8
    let pausePoll: Promise<void> | undefined
    let releasePoll!: () => void
    let pollPaused = false
    vi.spyOn(bridge, 'poll').mockImplementation(async () => {
      const sample = level
      if (pausePoll) {
        pollPaused = true
        await pausePoll
      }
      return { ...(await poll()), levels: [sample] }
    })
    const app = render(<App bridge={bridge} />)
    const readout = () => app.container.querySelector('.bl-meter-readout')
    const tick = () => app.container.querySelector<HTMLElement>('.bl-meter-peak')
    try {
      await act(() => vi.advanceTimersByTimeAsync(0))
      await act(() => vi.advanceTimersByTimeAsync(0))
      await act(() => vi.advanceTimersByTimeAsync(350))
      expect(readout()).toHaveTextContent('-12 dB')
      level = 0
      await act(() => vi.advanceTimersByTimeAsync(1_900))
      expect(readout()).toHaveTextContent('-12 dB')
      await act(() => vi.advanceTimersByTimeAsync(400))
      expect(readout()).toHaveTextContent(/^-[2-5]\d dB$/)
      expect(parseFloat(tick()?.style.left ?? '')).toBeLessThan(80)
      await act(() => vi.advanceTimersByTimeAsync(750))
      expect(readout()).toHaveTextContent('-∞')
      expect(tick()).toHaveStyle({ opacity: '0' })

      level = 0.8
      await act(() => vi.advanceTimersByTimeAsync(350))
      expect(readout()).toHaveTextContent('-12 dB')
      pausePoll = new Promise(resolve => {
        releasePoll = resolve
      })
      await act(() => vi.advanceTimersByTimeAsync(60))
      expect(pollPaused).toBe(true)
      fireEvent.change(screen.getByRole('combobox', { name: 'Microphone' }), {
        target: { value: 'built-in' },
      })
      expect(readout()).toHaveTextContent('-∞')
      expect(tick()).toHaveStyle({ opacity: '0' })
      level = 0
      pausePoll = undefined
      await act(async () => {
        releasePoll()
        await vi.advanceTimersByTimeAsync(350)
      })
      expect(readout()).toHaveTextContent('-∞')
      expect(tick()).toHaveStyle({ opacity: '0' })
    } finally {
      app.unmount()
      vi.useRealTimers()
    }
  })

  it('keeps the projector action beside its select in both views', async () => {
    const bridge = createDemoBridge()
    const init = bridge.init
    const automatic = 'Automatic → DISPLAY2 (1920x1080)'
    vi.spyOn(bridge, 'init').mockImplementation(async () => {
      const result = await init()
      result.options.projector[0] = { value: null, label: automatic }
      return result
    })
    const showProjector = vi.spyOn(bridge, 'show_projector')
    render(<App bridge={bridge} />)
    const projector = await screen.findByRole('combobox', { name: 'Projector' })
    const show = screen.getByRole('button', { name: 'Show on projector' })
    expect(projector).toHaveAttribute('title', automatic)
    expect(show).toBeDisabled()
    expect(show).toHaveAttribute('title', 'Start a session first to show the projector.')
    const open = screen.getByRole('button', { name: 'Open operator' })
    expect(open).toBeDisabled()
    expect(open).toHaveAttribute('title')
    expect(projector.closest('label')?.nextElementSibling).toBe(show)

    fireEvent.click(screen.getByRole('button', { name: 'Advanced' }))
    expect(
      screen.getByRole('combobox', { name: 'Projector' }).closest('label')?.nextElementSibling
    ).toBe(show)
    fireEvent.click(screen.getByRole('button', { name: 'Start session' }))
    await waitFor(() => expect(show).toBeEnabled(), { timeout: 3000 })
    expect(open).toBeEnabled()
    expect(show).toHaveAttribute(
      'title',
      'Opens the audience display full-screen on the projector, or brings it back to the front if it is already open'
    )
    fireEvent.click(show)
    expect(showProjector).toHaveBeenCalledOnce()
  })

  it('toggles the audience QR code beside the projector and shows the operator code in Network', async () => {
    const bridge = createDemoBridge()
    const init = bridge.init
    vi.spyOn(bridge, 'init').mockImplementation(async () => {
      const result = await init()
      result.form.allow_network = true
      return result
    })
    const setAudienceQr = vi.spyOn(bridge, 'set_audience_qr')
    render(<App bridge={bridge} />)
    await screen.findByRole('combobox', { name: 'Projector' })
    // No session, no address: no toggle.
    expect(screen.queryByRole('button', { name: 'QR code on the projector' })).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: 'Start session' }))
    const toggle = await screen.findByRole(
      'button',
      { name: 'QR code on the projector' },
      { timeout: 3000 }
    )
    const show = screen.getByRole('button', { name: 'Show on projector' })
    const identify = screen.getByRole('button', { name: 'Identify screens' })
    expect(show.nextElementSibling).toBe(identify)
    expect(identify.nextElementSibling).toBe(toggle)
    expect(toggle).toHaveAttribute('aria-pressed', 'false')
    fireEvent.click(toggle)
    expect(setAudienceQr).toHaveBeenLastCalledWith(true)
    await waitFor(() => expect(toggle).toHaveAttribute('aria-pressed', 'true'), { timeout: 3000 })
    fireEvent.click(toggle)
    expect(setAudienceQr).toHaveBeenLastCalledWith(false)
    await waitFor(() => expect(toggle).toHaveAttribute('aria-pressed', 'false'), { timeout: 3000 })

    fireEvent.click(screen.getByRole('button', { name: 'Advanced' }))
    fireEvent.click(screen.getByRole('tab', { name: 'Network' }))
    const code = screen.getByRole('img', { name: 'Operator page QR code (signs in)' })
    expect(code.getAttribute('src')).toMatch(/^data:image\//)
    expect(screen.getByText(/Never show it on the projector or the stream/)).toBeVisible()

    fireEvent.click(screen.getByRole('button', { name: 'Stop session' }))
    await waitFor(() =>
      expect(screen.queryByRole('button', { name: 'QR code on the projector' })).toBeNull()
    )
  })

  it('makes a new operator token only after confirming, and only while stopped', async () => {
    const bridge = createDemoBridge()
    const regenerate = vi
      .spyOn(bridge, 'regenerate_token')
      .mockResolvedValueOnce({ ok: true })
      .mockResolvedValueOnce({ ok: false })
    render(<App bridge={bridge} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Advanced' }))
    fireEvent.click(screen.getByRole('tab', { name: 'Network' }))
    // Local only: no token, no button.
    expect(screen.queryByRole('button', { name: 'New operator token' })).toBeNull()
    fireEvent.change(screen.getByLabelText('Access'), { target: { value: 'true' } })

    fireEvent.click(screen.getByRole('button', { name: 'New operator token' }))
    expect(screen.getByText(/lose operator access until they enter the new one/)).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(regenerate).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: 'New operator token' }))
    fireEvent.click(screen.getByRole('button', { name: 'Make new token' }))
    expect(await screen.findByText('New token made. It works from the next Start.')).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: 'New operator token' }))
    fireEvent.click(screen.getByRole('button', { name: 'Make new token' }))
    expect(
      await screen.findByText('Could not make a new token. The current one still works.')
    ).toHaveClass('bl-error')
    expect(regenerate).toHaveBeenCalledTimes(2)

    fireEvent.click(screen.getByRole('button', { name: 'Start session' }))
    expect(screen.getByRole('button', { name: 'New operator token' })).toBeDisabled()
    expect(screen.getByText('Stop BibleLive to make a new token.')).toBeVisible()
  })

  it('slows down when hidden and coalesces resume and action polls behind an in-flight poll', async () => {
    vi.useFakeTimers()
    let hidden = false
    const visibility = vi.spyOn(document, 'hidden', 'get').mockImplementation(() => hidden)
    const bridge = createDemoBridge()
    const originalPoll = bridge.poll
    const poll = vi.spyOn(bridge, 'poll')
    const app = render(<App bridge={bridge} />)
    try {
      await act(() => vi.advanceTimersByTimeAsync(0))
      expect(poll).toHaveBeenCalledTimes(1)
      hidden = true
      fireEvent(document, new Event('visibilitychange'))
      await act(() => vi.advanceTimersByTimeAsync(999))
      expect(poll).toHaveBeenCalledTimes(1)

      let finish = () => {}
      poll.mockImplementationOnce(
        () =>
          new Promise(resolve => {
            finish = () => resolve(originalPoll())
          })
      )
      await act(() => vi.advanceTimersByTimeAsync(1))
      expect(poll).toHaveBeenCalledTimes(2)
      hidden = false
      fireEvent(document, new Event('visibilitychange'))
      fireEvent.click(screen.getByRole('button', { name: 'Refresh devices' }))
      await act(() => vi.advanceTimersByTimeAsync(0))
      expect(poll).toHaveBeenCalledTimes(2)
      await act(async () => finish())
      await act(() => vi.advanceTimersByTimeAsync(0))
      expect(poll).toHaveBeenCalledTimes(3)

      fireEvent.click(screen.getByRole('button', { name: 'Refresh devices' }))
      await act(() => vi.advanceTimersByTimeAsync(0))
      expect(poll).toHaveBeenCalledTimes(4)
      hidden = true
      fireEvent(document, new Event('visibilitychange'))
      hidden = false
      fireEvent(document, new Event('visibilitychange'))
      expect(poll).toHaveBeenCalledTimes(5)
    } finally {
      app.unmount()
      visibility.mockRestore()
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
