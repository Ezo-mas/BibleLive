import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react'
import { useIdleScreen } from './hooks/useIdleScreen'
import type { IdleScreenState } from './hooks/useIdleScreen'
import { IdleScreenSettings } from './components/IdleScreenSettings'
import { IDLE_SCREEN_MAX_BYTES, parseIdleScreenInfo } from './idleScreenApi'

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as unknown as Response
}

/** What the fake server holds: the fit mode and whether a picture is set. */
let server: { fit: 'logo' | 'fill'; image: { version: string } | null } = {
  fit: 'logo',
  image: null,
}

/** Stand-in for the idle-screen routes; `refuse` makes every request fail with that status. */
function stubFetch(refuse: { status: number; detail?: string } | null = null) {
  const fetchMock = vi.fn(async (_url: RequestInfo | URL, init?: RequestInit) => {
    if (refuse) return jsonResponse({ detail: refuse.detail ?? 'refused' }, refuse.status)
    const method = init?.method ?? 'GET'
    if (method === 'POST') {
      server = { ...server, image: { version: 'aaaaaaaaaaaaaaaa' } }
    } else if (method === 'PUT') {
      server = { ...server, fit: (JSON.parse(String(init?.body)) as { fit: 'logo' | 'fill' }).fit }
    } else if (method === 'DELETE') {
      server = { ...server, image: null }
    }
    return jsonResponse(server)
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

/** A picture file of the given size; only its size and type matter to the console. */
function pictureFile(size = 1024): File {
  const file = new File(['x'], 'logo.png', { type: 'image/png' })
  Object.defineProperty(file, 'size', { value: size })
  return file
}

/** The hook with the setting already read, as it is once the settings dialog has opened. */
async function loadedHook(token: string | null = null) {
  const hook = renderHook(() => useIdleScreen(token))
  act(() => hook.result.current.refresh())
  await waitFor(() => expect(hook.result.current.info).not.toBeNull())
  return hook
}

afterEach(() => {
  vi.unstubAllGlobals()
  server = { fit: 'logo', image: null }
})

describe('parseIdleScreenInfo', () => {
  it('accepts a blank screen and a picture', () => {
    expect(parseIdleScreenInfo({ fit: 'logo', image: null })).toEqual({ fit: 'logo', image: null })
    expect(parseIdleScreenInfo({ fit: 'fill', image: { version: 'aaaaaaaaaaaaaaaa' } })).toEqual({
      fit: 'fill',
      image: { version: 'aaaaaaaaaaaaaaaa' },
    })
  })

  it.each([
    ['an unknown fit', { fit: 'stretch', image: null }],
    ['no image field', { fit: 'logo' }],
    ['a picture without a version', { fit: 'logo', image: {} }],
    ['not an object', 'logo'],
  ])('rejects %s', (_label, body) => {
    expect(parseIdleScreenInfo(body)).toBeNull()
  })
})

describe('useIdleScreen', () => {
  it('reads nothing on its own: the settings dialog asks when it opens', () => {
    const fetchMock = stubFetch()
    renderHook(() => useIdleScreen(null))
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('reads the setting when asked, with the operator token', async () => {
    const fetchMock = stubFetch()
    const { result } = renderHook(() => useIdleScreen('tok'))

    act(() => result.current.refresh())

    await waitFor(() => expect(result.current.info).toEqual({ fit: 'logo', image: null }))
    expect(fetchMock).toHaveBeenCalledWith('/api/idle-screen', {
      headers: { Authorization: 'Bearer tok' },
    })
  })

  it('sends the picture file itself as the body, and shows the new version', async () => {
    const fetchMock = stubFetch()
    const { result } = await loadedHook()
    const file = pictureFile()

    act(() => result.current.upload(file))

    await waitFor(() => expect(result.current.info?.image?.version).toBe('aaaaaaaaaaaaaaaa'))
    const [url, init] = fetchMock.mock.calls[1] as unknown as [string, RequestInit]
    expect(url).toBe('/api/idle-screen')
    expect(init.method).toBe('POST')
    expect(init.body).toBe(file)
  })

  it('refuses a file over the cap before sending anything', async () => {
    const fetchMock = stubFetch()
    const { result } = await loadedHook()
    const callsBefore = fetchMock.mock.calls.length

    act(() => result.current.upload(pictureFile(IDLE_SCREEN_MAX_BYTES + 1)))

    expect(result.current.error).toBe('The picture is larger than 8 MB.')
    expect(fetchMock.mock.calls.length).toBe(callsBefore)
  })

  it('shows the console’s own wording for a format refusal, not the server’s', async () => {
    stubFetch({ status: 415, detail: 'Only PNG, JPEG and WebP pictures are accepted.' })
    const { result } = renderHook(() => useIdleScreen(null))

    act(() => result.current.upload(pictureFile()))

    await waitFor(() => expect(result.current.error).toBe('Use a PNG, JPEG or WebP picture.'))
    expect(result.current.error).not.toMatch(/Only PNG/)
  })

  it('tells the operator to sign in again when the token is refused', async () => {
    stubFetch({ status: 401 })
    const { result } = renderHook(() => useIdleScreen(null))

    act(() => result.current.refresh())

    await waitFor(() =>
      expect(result.current.error).toBe('Not authorized. Sign in again with your operator token.')
    )
  })

  it('changes the fit mode from the server’s confirmation', async () => {
    const fetchMock = stubFetch()
    const { result } = await loadedHook()

    act(() => result.current.changeFit('fill'))

    await waitFor(() => expect(result.current.info?.fit).toBe('fill'))
    const [, init] = fetchMock.mock.calls[1] as unknown as [string, RequestInit]
    expect(init.method).toBe('PUT')
    expect(JSON.parse(String(init.body))).toEqual({ fit: 'fill' })
  })

  it('sends nothing when the chosen fit is already in force', async () => {
    const fetchMock = stubFetch()
    const { result } = await loadedHook()
    const callsBefore = fetchMock.mock.calls.length

    act(() => result.current.changeFit('logo'))

    expect(fetchMock.mock.calls.length).toBe(callsBefore)
  })

  it('removes the picture, so the display goes back to blank', async () => {
    server = { fit: 'logo', image: { version: 'aaaaaaaaaaaaaaaa' } }
    const fetchMock = stubFetch()
    const { result } = await loadedHook()

    act(() => result.current.remove())

    await waitFor(() => expect(result.current.info?.image).toBeNull())
    const [, init] = fetchMock.mock.calls[1] as unknown as [string, RequestInit]
    expect(init.method).toBe('DELETE')
  })
})

function stateWith(overrides: Partial<IdleScreenState> = {}): IdleScreenState {
  return {
    info: { fit: 'logo', image: null },
    error: null,
    isBusy: false,
    refresh: vi.fn(),
    upload: vi.fn(),
    changeFit: vi.fn(),
    remove: vi.fn(),
    ...overrides,
  }
}

describe('IdleScreenSettings', () => {
  it('says the display is blank when there is no picture, and offers nothing to remove', () => {
    render(<IdleScreenSettings state={stateWith()} disabled={false} />)

    expect(
      screen.getByText('No picture: the display is blank while nothing is on screen.')
    ).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Remove picture' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Upload picture' })).toBeEnabled()
  })

  it('previews the picture in the chosen fit mode', () => {
    render(
      <IdleScreenSettings
        state={stateWith({ info: { fit: 'fill', image: { version: 'aaaaaaaaaaaaaaaa' } } })}
        disabled={false}
      />
    )

    const preview = screen.getByTestId('idle-screen-preview')
    expect(preview).toHaveClass('idle-screen-preview--fill')
    expect(preview.querySelector('img')).toHaveAttribute(
      'src',
      '/api/idle-screen/image?v=aaaaaaaaaaaaaaaa'
    )
    expect(screen.getByRole('button', { name: 'Fill screen' })).toHaveAttribute(
      'aria-pressed',
      'true'
    )
    expect(screen.getByRole('button', { name: 'Logo' })).toHaveAttribute('aria-pressed', 'false')
    expect(screen.getByRole('button', { name: 'Replace picture' })).toBeInTheDocument()
  })

  it('changes the fit mode when a button is pressed', () => {
    const state = stateWith()
    render(<IdleScreenSettings state={state} disabled={false} />)

    fireEvent.click(screen.getByRole('button', { name: 'Fill screen' }))

    expect(state.changeFit).toHaveBeenCalledWith('fill')
  })

  it('passes the chosen file on for upload', () => {
    const state = stateWith()
    const { container } = render(<IdleScreenSettings state={state} disabled={false} />)
    const file = pictureFile()

    fireEvent.change(container.querySelector('input[type="file"]') as HTMLInputElement, {
      target: { files: [file] },
    })

    expect(state.upload).toHaveBeenCalledWith(file)
  })

  it('offers only the picture formats the server accepts in the picker', () => {
    const { container } = render(<IdleScreenSettings state={stateWith()} disabled={false} />)

    expect(container.querySelector('input[type="file"]')).toHaveAttribute(
      'accept',
      'image/png,image/jpeg,image/webp'
    )
  })

  it('is read-only while disconnected', () => {
    render(
      <IdleScreenSettings
        state={stateWith({ info: { fit: 'logo', image: { version: 'aaaaaaaaaaaaaaaa' } } })}
        disabled
      />
    )

    expect(screen.getByRole('button', { name: 'Logo' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Replace picture' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Remove picture' })).toBeDisabled()
  })

  it('shows a failure inline', () => {
    render(
      <IdleScreenSettings
        state={stateWith({ error: 'The picture could not be saved (HTTP 500).' })}
        disabled={false}
      />
    )

    expect(screen.getByText('The picture could not be saved (HTTP 500).')).toBeInTheDocument()
  })
})
