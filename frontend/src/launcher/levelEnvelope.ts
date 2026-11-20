import { LEVEL_INTERVAL, type LevelSample } from './noSignal'

const ATTACK_MS = 60
const RELEASE_MS = 800
const SILENCE = 0.001
const MAX_QUEUED_LEVELS = 6
export const RIPPLE_STEP_MS = 50

export const MIN_DBFS = -60
export const MAX_DBFS = 0
export const YELLOW_DBFS = -20
export const RED_DBFS = -9
export const METER_TICKS = [MIN_DBFS, -45, -30, YELLOW_DBFS, RED_DBFS, -3, MAX_DBFS].map(db => ({
  db,
  position: dbfsToLevel(db) * 100,
}))

export function levelToDbfs(level: number) {
  return MIN_DBFS + level * (MAX_DBFS - MIN_DBFS)
}

export function dbfsToLevel(db: number) {
  return (db - MIN_DBFS) / (MAX_DBFS - MIN_DBFS)
}

export function speechTargetPosition() {
  return { start: dbfsToLevel(-24) * 100, end: dbfsToLevel(-12) * 100 }
}

export function meterZone(db: number) {
  if (db < YELLOW_DBFS) return 'green'
  return db <= RED_DBFS ? 'yellow' : 'red'
}

export function formatPeak(level: number) {
  return level === 0 ? '-∞' : `${Math.round(levelToDbfs(level))} dB`
}

export function advanceEnvelope(level: number, target: number, elapsed: number) {
  if (elapsed <= 0) return level
  const decay = target > level ? 5 ** (-elapsed / ATTACK_MS) : 3 ** (-elapsed / RELEASE_MS)
  const next = target + (level - target) * decay
  return target === 0 && next < SILENCE ? 0 : next
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
