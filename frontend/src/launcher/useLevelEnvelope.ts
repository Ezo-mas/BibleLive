import { useCallback, useLayoutEffect, useRef } from 'react'
import { advanceEnvelope } from './levelEnvelope'

const PEAK_HOLD_MS = 1000

export function useLevelEnvelope(meterOn: boolean) {
  const waveRef = useRef<HTMLDivElement>(null)
  const meterRef = useRef<HTMLDivElement>(null)
  const pendingPeak = useRef(0)
  const updateReduced = useRef<(() => void) | null>(null)

  const acceptLevels = useCallback((levels: number[] | null) => {
    pendingPeak.current = levels ? Math.max(pendingPeak.current, ...levels) : 0
    updateReduced.current?.()
  }, [])

  useLayoutEffect(() => {
    const wave = waveRef.current
    const meter = meterRef.current
    if (!wave || !meter) return
    const bars = Array.from(wave.children) as HTMLElement[]
    const [fill, tick] = Array.from(meter.children) as HTMLElement[]

    function paint(level: number, peak: number, time: number, drift: boolean) {
      fill.style.transform = `scaleX(${level})`
      tick.style.left = `${peak * 100}%`
      tick.style.opacity = peak > 0 ? '1' : '0'
      meter?.setAttribute('aria-valuenow', level.toFixed(3))
      bars.forEach((bar, index) => {
        const phase = Math.min(index, bars.length - 1 - index)
        const variation = drift ? 1 + 0.08 * Math.sin(time / 420 + phase * 1.3) : 1
        bar.style.setProperty('--wave-level', String(level * variation))
      })
    }

    if (!meterOn) {
      pendingPeak.current = 0
      paint(0, 0, 0, false)
      return
    }

    const motion = window.matchMedia?.('(prefers-reduced-motion: reduce)')
    let frame = 0
    let level = 0
    let heldPeak = 0
    let holdUntil = 0
    let previous = performance.now()

    function update(now: number) {
      const sample = pendingPeak.current
      pendingPeak.current = 0
      const next = advanceEnvelope(level, sample, now - previous, meterOn)
      const releasedPeak = advanceEnvelope(
        heldPeak,
        0,
        Math.max(0, now - Math.max(holdUntil, previous)),
        meterOn
      )
      if (sample > 0 && sample >= releasedPeak) holdUntil = now + PEAK_HOLD_MS
      const peak = Math.max(next, releasedPeak)
      if (level || heldPeak || next || peak) paint(next, peak, now, !motion?.matches)
      level = next
      heldPeak = peak
      previous = now
    }

    function animate(now: number) {
      update(now)
      frame = window.requestAnimationFrame(animate)
    }

    function configureMotion() {
      window.cancelAnimationFrame(frame)
      updateReduced.current = motion?.matches ? () => update(performance.now()) : null
      if (updateReduced.current) updateReduced.current()
      else frame = window.requestAnimationFrame(animate)
    }

    configureMotion()
    motion?.addEventListener('change', configureMotion)
    return () => {
      window.cancelAnimationFrame(frame)
      motion?.removeEventListener('change', configureMotion)
      updateReduced.current = null
    }
  }, [meterOn])

  return { waveRef, meterRef, acceptLevels }
}
