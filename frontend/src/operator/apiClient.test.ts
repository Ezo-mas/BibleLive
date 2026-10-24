import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, authHeaders, request } from './apiClient'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('authHeaders', () => {
  it('sends the bearer token when there is one', () => {
    expect(authHeaders('secret')).toEqual({ Authorization: 'Bearer secret' })
  })

  it('sends no Authorization header for a null or empty token', () => {
    expect(authHeaders(null)).toEqual({})
    expect(authHeaders('')).toEqual({})
  })

  it('adds the JSON content type only when the body is JSON', () => {
    expect(authHeaders('secret', true)).toEqual({
      Authorization: 'Bearer secret',
      'Content-Type': 'application/json',
    })
    expect(authHeaders(null, true)).toEqual({ 'Content-Type': 'application/json' })
  })
})

describe('request', () => {
  it('passes the request through and returns a refusal for the caller to check', async () => {
    const fetchMock = vi.fn(async () => new Response(null, { status: 403 }))
    vi.stubGlobal('fetch', fetchMock)

    const init = { headers: authHeaders('secret') }
    const response = await request('/api/x', init, 'Cannot reach the server')

    expect(response.status).toBe(403)
    expect(fetchMock).toHaveBeenCalledWith('/api/x', init)
  })

  it('throws an ApiError with status 0 and the given message when no answer comes', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new TypeError('network down')
      })
    )

    const failure = request('/api/x', {}, 'Cannot reach the server')
    await expect(failure).rejects.toBeInstanceOf(ApiError)
    await expect(failure).rejects.toMatchObject({ status: 0, message: 'Cannot reach the server' })
  })
})
