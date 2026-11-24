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

// Mirrors the backend's token redaction (a `token` query parameter or `#token=` fragment), so
// copied logs never carry the operator token even if a line were to.
const tokenValue = /([?&#]token=)[^&\s"']*/gi

export function logText(
  lines: LogLine[],
  label: {
    level: (level: LogLine['level']) => string
    source: (source: LogLine['source']) => string
  }
) {
  return lines
    .map(line =>
      `${line.time} ${label.level(line.level)} ${label.source(line.source)} ${line.text}`.replace(
        tokenValue,
        '$1[REDACTED]'
      )
    )
    .join('\n')
}
