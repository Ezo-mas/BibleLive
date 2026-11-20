import type { LogLine } from './bridge'

export const logLevels = ['DEBUG', 'INFO', 'WARNING', 'ERROR'] as const
export const logSources = ['all', 'launcher', 'server', 'audio'] as const
export type LogSourceFilter = (typeof logSources)[number]

export function filterLog(lines: LogLine[], source: LogSourceFilter, minimum: LogLine['level']) {
  const minimumRank = logLevels.indexOf(minimum)
  return lines.filter(
    line =>
      (source === 'all' || line.source === source) && logLevels.indexOf(line.level) >= minimumRank
  )
}
