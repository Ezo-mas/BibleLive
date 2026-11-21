import {
  dbfsToLevel,
  LEVEL_INTERVAL,
  MAX_DBFS,
  MIN_DBFS,
  RED_DBFS,
  YELLOW_DBFS,
  type LevelSample,
} from '../shared/levels'

const ATTACK_MS = 60
const RELEASE_MS = 625
const PEAK_HOLD_MS = 2_000
const PEAK_RELEASE_DB_PER_MS = 0.06
const SILENCE = 0.001
const MAX_QUEUED_LEVELS = 6
export const RIPPLE_STEP_MS = 40

export const METER_TICKS = [MIN_DBFS, -45, -30, YELLOW_DBFS, RED_DBFS, -3, MAX_DBFS].map(db => ({
  db,
  position: dbfsToLevel(db) * 100,
}))

export function speechTargetPosition() {
  return { start: dbfsToLevel(-24) * 100, end: dbfsToLevel(-12) * 100 }
}

export function advanceEnvelope(level: number, target: number, elapsed: number) {
  if (elapsed <= 0) return level
  const decay = target > level ? 5 ** (-elapsed / ATTACK_MS) : 3 ** (-elapsed / RELEASE_MS)
  const next = target + (level - target) * decay
  return target === 0 && next < SILENCE ? 0 : next
}

export function releasePeak(peak: number, signalEnd: number, from: number, to: number) {
  const elapsed = Math.max(0, to - Math.max(from, signalEnd + PEAK_HOLD_MS))
  const drop = (elapsed * PEAK_RELEASE_DB_PER_MS) / (MAX_DBFS - MIN_DBFS)
  return Math.max(0, peak - drop)
}

export function queueLevels(queue: LevelSample[], levels: number[], now: number) {
  if (!levels.length) return queue
  if (queue.length + levels.length > MAX_QUEUED_LEVELS) {
    return [{ time: now, level: levels[levels.length - 1] }]
  }
  const last = queue[queue.length - 1]
  const start = last ? Math.max(now, last.time + LEVEL_INTERVAL) : now
  return [
    ...queue,
    ...levels.map((level, index) => ({ level, time: start + index * LEVEL_INTERVAL })),
  ]
}

export function playLevels(level: number, queue: LevelSample[], from: number, to: number) {
  let time = from
  for (const sample of queue) {
    const start = Math.max(time, sample.time)
    const end = Math.min(to, sample.time + LEVEL_INTERVAL)
    if (start >= end) continue
    level = advanceEnvelope(level, 0, start - time)
    level = advanceEnvelope(level, sample.level, end - start)
    time = end
  }
  return advanceEnvelope(level, 0, to - time)
}

export function rippleLevel(history: LevelSample[], now: number, distance: number) {
  const time = now - distance * RIPPLE_STEP_MS
  const nextIndex = history.findIndex(sample => sample.time >= time)
  const next = history[nextIndex]
  if (!next) return history[history.length - 1]?.level ?? 0
  if (next.time === time) return next.level
  const previous = history[nextIndex - 1]
  if (!previous) return 0
  const fraction = (time - previous.time) / (next.time - previous.time)
  return previous.level + (next.level - previous.level) * fraction
}

export function rippleDistance(index: number, barCount: number) {
  return Math.floor(Math.abs(index - (barCount - 1) / 2))
}
