import { useEffect, useRef, useState } from 'react'
import { SCREEN_SWAP, VERSE_SWAP } from './fadeTimings'
import type { SwapTiming } from './fadeTimings'

/**
 * Fade-then-swap for the band: `swap(next)` fades out what is shown, then puts `next`
 * in and fades it in (`null` leaves nothing). A newer swap cancels one in flight.
 * Passage to passage is quick (VERSE_SWAP); the band coming or going takes SCREEN_SWAP.
 */
export function useFadeSwap<T>(): {
  shown: T | null
  visible: boolean
  timing: SwapTiming
  swap: (next: T | null) => void
} {
  const [shown, setShown] = useState<T | null>(null)
  const [visible, setVisible] = useState(false)
  const [timing, setTiming] = useState<SwapTiming>(SCREEN_SWAP)
  const timerRef = useRef<number | undefined>(undefined)
  // Read by `swap`, which a caller may hold from an earlier render (an interval).
  const upRef = useRef(false)
  upRef.current = shown !== null && visible

  useEffect(() => () => window.clearTimeout(timerRef.current), [])

  const swap = (next: T | null) => {
    window.clearTimeout(timerRef.current)
    const nextTiming = upRef.current && next !== null ? VERSE_SWAP : SCREEN_SWAP
    setTiming(nextTiming)
    setVisible(false)
    timerRef.current = window.setTimeout(() => {
      setShown(next)
      setVisible(next !== null)
    }, nextTiming.exitMs)
  }
  return { shown, visible, timing, swap }
}
