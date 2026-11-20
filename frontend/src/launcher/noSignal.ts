import type { Meter } from './bridge'

export const LEVEL_INTERVAL = 50
const QUIET_WINDOW = 5_000
export const SIGNAL_THRESHOLD = 0.1

export interface LevelSample {
  time: number
  level: number
}

export function noSignal(meter: Meter, samples: LevelSample[], now: number): boolean {
  if (meter !== 'on' || !samples.length) return false
  const cutoff = now - QUIET_WINDOW
  return (
    samples[0].time <= cutoff &&
    samples[samples.length - 1].time > now - 2 * LEVEL_INTERVAL &&
    samples.every(sample => sample.time <= cutoff || sample.level < SIGNAL_THRESHOLD)
  )
}

export function appendLevels(previous: LevelSample[], levels: number[], now: number) {
  // The bridge delivers 50 ms blocks without timestamps. Place the newest at receipt time.
  const samples = levels.map((level, index) => ({
    level,
    time: now - (levels.length - 1 - index) * LEVEL_INTERVAL,
  }))
  const last = previous[previous.length - 1]?.time
  const history =
    last !== undefined && (samples[0]?.time ?? now) - last <= 3 * LEVEL_INTERVAL ? previous : []
  // Keep the last sample at or before the cutoff to establish a full five-second window.
  const combined = [...history, ...samples]
  const cutoff = now - QUIET_WINDOW
  let first = 0
  while (first + 1 < combined.length && combined[first + 1].time <= cutoff) first++
  return combined.slice(first)
}
