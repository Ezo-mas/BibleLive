import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react'
import { useBibleVersion } from './hooks/useBibleVersion'
import { BibleVersionSettings } from './components/BibleVersionSettings'
import { jsonResponse } from '../test/operatorHarness'

let server = { version: 'kjv', available: ['kjv', 'ltb'] }

function stubFetch() {
  const fetchMock = vi.fn(async (_url: RequestInfo | URL, init?: RequestInit) => {
    if (init?.method === 'POST') {
      const { version } = JSON.parse(String(init.body)) as { version: string }
      if (!server.available.includes(version)) {
        return jsonResponse({ detail: `Bible translation '${version}' is not available.` }, 404)
      }
      server = { ...server, version }
    }
    return jsonResponse(server)
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

afterEach(() => {
  server = { version: 'kjv', available: ['kjv', 'ltb'] }
  vi.unstubAllGlobals()
})

describe('useBibleVersion', () => {
  it('reads the version once connected and switches only on the server’s confirmation', async () => {
    stubFetch()
    const notify = vi.fn()
    const { result } = renderHook(() => useBibleVersion(null, true, notify))

    await waitFor(() => expect(result.current.version).toBe('kjv'))
    expect(result.current.available).toEqual(['kjv', 'ltb'])

    act(() => result.current.change('ltb'))
    await waitFor(() => expect(result.current.version).toBe('ltb'))
    expect(notify).toHaveBeenCalledWith('info', 'Bible version: Lithuanian (LTB)')
  })

  it('does not read while disconnected', () => {
    const fetchMock = stubFetch()
    renderHook(() => useBibleVersion(null, false, vi.fn()))
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('shows the server’s refusal and keeps the old version', async () => {
    stubFetch()
    const notify = vi.fn()
    const { result } = renderHook(() => useBibleVersion(null, true, notify))
    await waitFor(() => expect(result.current.version).toBe('kjv'))

    act(() => result.current.change('nope'))
    await waitFor(() => expect(result.current.error).toMatch(/not available/))
    expect(result.current.version).toBe('kjv')
    expect(notify).toHaveBeenCalledWith('error', expect.stringMatching(/not available/))
  })

  it('announces a change pushed over the socket, once', async () => {
    stubFetch()
    const notify = vi.fn()
    const { result } = renderHook(() => useBibleVersion(null, true, notify))
    await waitFor(() => expect(result.current.version).toBe('kjv'))

    act(() => result.current.applyConfirmed({ version: 'ltb', available: ['kjv', 'ltb'] }))
    expect(result.current.version).toBe('ltb')
    expect(notify).toHaveBeenCalledTimes(1)

    act(() => result.current.applyConfirmed({ version: 'ltb', available: ['kjv', 'ltb'] }))
    expect(notify).toHaveBeenCalledTimes(1)

    act(() => result.current.applyConfirmed({ nonsense: true }))
    expect(result.current.version).toBe('ltb')
  })

  it('does not let a late switch reply overwrite a newer push', async () => {
    const all = ['kjv', 'ltb', 'web']
    let releasePost: (() => void) | null = null
    vi.stubGlobal(
      'fetch',
      vi.fn(async (_url: RequestInfo | URL, init?: RequestInit) => {
        if (init?.method === 'POST') {
          await new Promise<void>(resolve => {
            releasePost = resolve
          })
          return jsonResponse({ version: 'ltb', available: all })
        }
        return jsonResponse({ version: 'kjv', available: all })
      })
    )
    const { result } = renderHook(() => useBibleVersion(null, true, vi.fn()))
    await waitFor(() => expect(result.current.version).toBe('kjv'))

    act(() => result.current.change('ltb'))
    // Another operator moves to WEB while our request is still in flight...
    act(() => result.current.applyConfirmed({ version: 'web', available: all }))
    // ...and our (older) answer arrives afterwards.
    await act(async () => {
      releasePost?.()
    })
    await waitFor(() => expect(result.current.isSwitching).toBe(false))
    expect(result.current.version).toBe('web')
  })
})

describe('BibleVersionSettings', () => {
  const base = {
    version: 'kjv' as string | null,
    available: ['kjv', 'ltb'],
    error: null,
    isSwitching: false,
    refresh: vi.fn(),
    applyConfirmed: vi.fn(),
  }

  it('marks the active version and asks to switch to another', () => {
    const change = vi.fn()
    render(<BibleVersionSettings state={{ ...base, change }} disabled={false} />)

    expect(screen.getByRole('button', { name: 'KJV' })).toHaveAttribute('aria-pressed', 'true')
    fireEvent.click(screen.getByRole('button', { name: 'LTB' }))
    expect(change).toHaveBeenCalledWith('ltb')
  })

  it('is read-only while disconnected', () => {
    render(<BibleVersionSettings state={{ ...base, change: vi.fn() }} disabled />)
    expect(screen.getByRole('button', { name: 'LTB' })).toBeDisabled()
  })
})
