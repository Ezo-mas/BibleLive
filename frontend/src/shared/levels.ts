export const LEVEL_INTERVAL = 50 // 800 samples at 16 kHz, matching backend/audio/levels.py.
export const SIGNAL_THRESHOLD = 0.1

export interface LevelSample {
  time: number
  level: number
}

export const MIN_DBFS = -60
export const MAX_DBFS = 0
export const YELLOW_DBFS = -20
export const RED_DBFS = -9

export function levelToDbfs(level: number) {
  return MIN_DBFS + level * (MAX_DBFS - MIN_DBFS)
}

export function formatPeak(level: number) {
  return level === 0 ? '-∞' : `${Math.round(levelToDbfs(level))} dB`
}

export function dbfsToLevel(db: number) {
  return (db - MIN_DBFS) / (MAX_DBFS - MIN_DBFS)
}

export function meterZone(db: number) {
  if (db < YELLOW_DBFS) return 'green'
  return db <= RED_DBFS ? 'yellow' : 'red'
}
