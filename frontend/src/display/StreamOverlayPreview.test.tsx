import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { act, render, screen } from '@testing-library/react'
import StreamOverlayPreview, { PREVIEW_HOLD_MS } from './StreamOverlayPreview'
import { SCREEN_SWAP, VERSE_SWAP } from './fadeTimings'

beforeEach(() => {
  vi.useFakeTimers()
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe = vi.fn()
      disconnect = vi.fn()
    }
  )
  window.history.replaceState(null, '', '/display?mode=obs&preview=1&position=top&font=sans')
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
  window.history.replaceState(null, '', '/')
})

it('opens no socket and cycles sample passages with the URL layout', () => {
  const socket = vi.fn()
  vi.stubGlobal('WebSocket', socket)
  render(<StreamOverlayPreview />)
  expect(socket).not.toHaveBeenCalled()

  act(() => {
    vi.advanceTimersByTime(SCREEN_SWAP.exitMs)
  })
  const band = screen.getByTestId('stream-band')
  expect(band).toHaveClass('stream-band--top', 'stream-band--sans', 'stream-band--visible')
  expect(screen.getByText('John 3:16')).toBeInTheDocument()

  // The next sample is due PREVIEW_HOLD_MS after mount.
  act(() => {
    vi.advanceTimersByTime(PREVIEW_HOLD_MS - SCREEN_SWAP.exitMs)
  })
  expect(screen.getByTestId('stream-band')).toHaveClass('stream-band--hidden')
  // Sample to sample is a passage-to-passage swap: the quick one.
  act(() => {
    vi.advanceTimersByTime(VERSE_SWAP.exitMs)
  })
  expect(screen.getByText('Psalms 23:1-3')).toBeInTheDocument()
  expect(screen.getByTestId('stream-band')).toHaveClass('stream-band--visible')
})
