import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Bridge } from './bridge'

describe('launcher bridge handshake', () => {
  const api = { init: vi.fn(), poll: vi.fn() } as unknown as Bridge
  const ready = () => window.dispatchEvent(new Event('pywebviewready'))

  beforeEach(() => {
    vi.resetModules()
    vi.stubEnv('DEV', false)
  })

  afterEach(() => {
    delete window.pywebview
    vi.unstubAllGlobals()
    window.history.replaceState(null, '', '/')
    vi.unstubAllEnvs()
    vi.useRealTimers()
  })

  it('waits for methods when pywebview has only injected an empty API', async () => {
    const partial = {} as Bridge
    window.pywebview = { api: partial }
    const { getBridge } = await import('./bridge')
    const connected = vi.fn()
    const connection = getBridge()
    void connection.then(connected)
    partial.init = api.init
    ready()
    await Promise.resolve()
    expect(connected).not.toHaveBeenCalled()

    partial.poll = api.poll
    ready()
    expect(await connection).toBe(partial)
  })

  it('uses an API whose ready event already fired', async () => {
    window.pywebview = { api }
    ready()
    const { getBridge } = await import('./bridge')
    expect(await getBridge()).toBe(api)
  })

  it('keeps waiting for a late real API in production', async () => {
    vi.useFakeTimers()
    const { getBridge } = await import('./bridge')
    const connected = vi.fn()
    const connection = getBridge()
    void connection.then(connected)
    await vi.advanceTimersByTimeAsync(20_000)
    expect(connected).not.toHaveBeenCalled()

    window.pywebview = { api }
    ready()
    expect(await connection).toBe(api)
  })

  it('uses the demo when requested through the URL', async () => {
    window.history.replaceState(null, '', '/launcher.html?demo')
    window.pywebview = { api }
    const { getBridge } = await import('./bridge')
    const { isDemoBridge } = await import('./demoBridge')
    expect(isDemoBridge(await getBridge())).toBe(true)
  })

  it('uses the demo in Vite development without pywebview', async () => {
    vi.stubEnv('DEV', true)
    const { getBridge } = await import('./bridge')
    const { isDemoBridge } = await import('./demoBridge')
    expect(isDemoBridge(await getBridge())).toBe(true)
  })

  it('waits for the real bridge in a native window served by Vite', async () => {
    vi.stubEnv('DEV', true)
    vi.stubGlobal('chrome', { webview: {} })
    const { getBridge } = await import('./bridge')
    const connection = getBridge()

    window.pywebview = { api }
    ready()
    expect(await connection).toBe(api)
  })
})
