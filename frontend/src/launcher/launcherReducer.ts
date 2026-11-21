import type { InitResult, LogLine, PollResult } from './bridge'
import { appendLevels } from './noSignal'
import type { LevelSample } from '../shared/levels'

export const LOG_LIMIT = 2_000

export interface LauncherData {
  state: InitResult['state']
  options: InitResult['options']
  meter: PollResult['meter']
  meterError: string | null
  levels: LevelSample[]
  log: LogLine[]
  logCount: number
  sessionStopped: boolean
  audioWasRunning: boolean
  now: number
}

type Action =
  | { type: 'poll'; result: PollResult; now: number }
  | { type: 'options'; options: InitResult['options'] }
  | { type: 'resetLevels' }

export function initialData({ state, options }: InitResult): LauncherData {
  return {
    state,
    options,
    meter: 'off',
    meterError: null,
    levels: [],
    log: [],
    logCount: 0,
    sessionStopped: false,
    audioWasRunning: state.audio === 'running',
    now: Date.now(),
  }
}

export function launcherReducer(data: LauncherData, action: Action): LauncherData {
  switch (action.type) {
    case 'options':
      return { ...data, options: action.options }
    case 'resetLevels':
      return { ...data, levels: [] }
    case 'poll': {
      const { result, now } = action
      return {
        state: result.state,
        options: result.options ? { ...data.options, ...result.options } : data.options,
        meter: result.meter,
        meterError: result.meter_error,
        levels: result.meter === 'on' ? appendLevels(data.levels, result.levels, now) : [],
        log: result.log.length ? [...data.log, ...result.log].slice(-LOG_LIMIT) : data.log,
        logCount: data.logCount + result.log.length,
        sessionStopped:
          result.state.session === 'idle' && (data.state.session !== 'idle' || data.sessionStopped),
        audioWasRunning:
          result.state.audio === 'running' ||
          (result.state.session !== 'idle' && data.audioWasRunning),
        now,
      }
    }
  }
}
