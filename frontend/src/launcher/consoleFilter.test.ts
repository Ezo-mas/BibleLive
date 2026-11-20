import { expect, it } from 'vitest'
import type { LogLine } from './bridge'
import { filterLog } from './consoleFilter'

it('filters by source and minimum severity without changing log order', () => {
  const log: LogLine[] = [
    { time: '10:00:00', source: 'audio', level: 'WARNING', text: 'one' },
    { time: '10:00:01', source: 'launcher', level: 'ERROR', text: 'two' },
    { time: '10:00:02', source: 'audio', level: 'INFO', text: 'three' },
    { time: '10:00:03', source: 'audio', level: 'ERROR', text: 'four' },
  ]
  expect(filterLog(log, 'audio', 'WARNING')).toEqual([log[0], log[3]])
  expect(filterLog(log, 'all', 'ERROR')).toEqual([log[1], log[3]])
  expect(filterLog(log, 'all', 'DEBUG')).toEqual(log)
})
