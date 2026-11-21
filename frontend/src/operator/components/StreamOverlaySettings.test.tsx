import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, within } from '@testing-library/react'
import { STREAM_OVERLAY_STORAGE_KEY, StreamOverlaySettings } from './StreamOverlaySettings'

beforeEach(() => {
  window.localStorage.clear()
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe = vi.fn()
      disconnect = vi.fn()
    }
  )
  // jsdom lays nothing out; give the preview a width so the scaled page is drawn.
  vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(480)
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

const link = () => screen.getByRole('textbox', { name: 'OBS link' }) as HTMLInputElement

it('starts with the default link and a socket-free preview of it', () => {
  render(<StreamOverlaySettings />)
  expect(link().value).toBe(`${window.location.origin}/display?mode=obs`)
  const preview = screen.getByTitle('Preview of the stream overlay')
  expect(preview).toHaveAttribute('src', '/display?mode=obs&preview=1')
  expect(preview.style.transform).toBe('scale(0.25)')
})

it('builds the link from the choices and remembers them in this browser', () => {
  const { unmount } = render(<StreamOverlaySettings />)
  fireEvent.click(within(screen.getByRole('group', { name: 'Position' })).getByText('Top'))
  fireEvent.click(within(screen.getByRole('group', { name: 'Verse font' })).getByText('Sans'))
  fireEvent.click(within(screen.getByRole('group', { name: 'Band height' })).getByText('40%'))

  expect(link().value).toBe(
    `${window.location.origin}/display?mode=obs&height=40&position=top&font=sans`
  )
  expect(screen.getByTitle('Preview of the stream overlay')).toHaveAttribute(
    'src',
    '/display?mode=obs&height=40&position=top&font=sans&preview=1'
  )
  expect(window.localStorage.getItem(STREAM_OVERLAY_STORAGE_KEY)).toBe(
    '?mode=obs&height=40&position=top&font=sans'
  )

  unmount()
  render(<StreamOverlaySettings />)
  expect(link().value).toContain('position=top')
})
