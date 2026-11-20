import { useCallback, useLayoutEffect, useRef } from 'react'
import {
  advanceEnvelope,
  playLevels,
  queueLevels,
  rippleDistance,
  rippleLevel,
  RIPPLE_STEP_MS,
} from './levelEnvelope'
import { LEVEL_INTERVAL, type LevelSample } from './noSignal'

const PEAK_HOLD_MS = 1000

export function useLevelEnvelope(meterOn: boolean) {
  const waveRef = useRef<HTMLDivElement>(null)
  const meterRef = useRef<HTMLDivElement>(null)
  const queue = useRef<LevelSample[]>([])
  const onPoll = useRef<((now: number, reset: boolean) => void) | null>(null)

  const acceptLevels = useCallback((levels: number[] | null) => {
    const now = performance.now()
    queue.current = levels ? queueLevels(queue.current, levels, now) : []
    onPoll.current?.(now, levels === null)
  }, [])

  useLayoutEffect(() => {
    const wave = waveRef.current
    const meter = meterRef.current
    if (!wave || !meter) return
    const bars = Array.from(wave.children) as HTMLElement[]
    const [fill, tick] = Array.from(meter.children) as HTMLElement[]

    const distances = bars.map((_, index) => rippleDistance(index, bars.length))
    const gains = distances.map(distance => 1 + 0.08 * Math.sin(distance * 1.3))
    const history: LevelSample[] = []

    function paint(level: number, peak: number, time: number, ripple: boolean) {
      fill.style.transform = `scaleX(${level})`
      tick.style.left = `${peak * 100}%`
      tick.style.opacity = peak > 0 ? '1' : '0'
      meter?.setAttribute('aria-valuenow', level.toFixed(3))
      bars.forEach((bar, index) => {
        const value = ripple ? rippleLevel(history, time, distances[index]) * gains[index] : level
        bar.style.setProperty('--wave-level', String(value))
      })
    }

    if (!meterOn) {
      queue.current = []
      paint(0, 0, 0, false)
      return
    }

    const motion = window.matchMedia?.('(prefers-reduced-motion: reduce)')
    let frame = 0
    let level = 0
    let heldPeak = 0
    let holdUntil = 0
    let previous = Math.min(performance.now(), queue.current[0]?.time ?? Infinity)

    function update(now: number) {
      if (now < previous) return
      const next = playLevels(level, queue.current, previous, now)
      queue.current = queue.current.filter(sample => sample.time + LEVEL_INTERVAL > now)
      heldPeak = advanceEnvelope(heldPeak, 0, now - Math.max(holdUntil, previous))
      if (next > heldPeak) {
        heldPeak = next
        holdUntil = now + PEAK_HOLD_MS
      }
      const ripple = !motion?.matches
      if (ripple) {
        history.push({ time: now, level: next })
        const cutoff = now - distances[0] * RIPPLE_STEP_MS
        while (history.length > 1 && history[1].time <= cutoff) history.shift()
      }
      paint(next, heldPeak, now, ripple)
      level = next
      previous = now
    }

    function animate() {
      update(performance.now())
      frame = window.requestAnimationFrame(animate)
    }

    function configureMotion() {
      window.cancelAnimationFrame(frame)
      history.length = 0
      if (motion?.matches) update(performance.now())
      else frame = window.requestAnimationFrame(animate)
    }

    onPoll.current = (now, reset) => {
      if (reset) {
        level = heldPeak = holdUntil = 0
        history.length = 0
        previous = now
        paint(0, 0, now, false)
      } else if (motion?.matches) {
        update(now)
      }
    }
    configureMotion()
    motion?.addEventListener('change', configureMotion)
    return () => {
      window.cancelAnimationFrame(frame)
      motion?.removeEventListener('change', configureMotion)
      onPoll.current = null
    }
  }, [meterOn])

  return { waveRef, meterRef, acceptLevels }
}
