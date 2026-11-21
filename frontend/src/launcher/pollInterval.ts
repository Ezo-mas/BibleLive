import type { Meter, State } from './bridge'
import { LEVEL_INTERVAL } from '../shared/levels'

export function pollInterval(session: State['session'], meter: Meter, hidden: boolean) {
  if (hidden) return 1000
  return session === 'idle' && (meter === 'on' || meter === 'starting') ? LEVEL_INTERVAL : 250
}
