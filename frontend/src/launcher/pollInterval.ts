import type { Meter, State } from './bridge'
import { LEVEL_INTERVAL } from '../shared/levels'

export function pollInterval(session: State['session'], meter: Meter, hidden: boolean) {
  if (hidden) return 1000
  if (session === 'idle' && (meter === 'on' || meter === 'starting')) return LEVEL_INTERVAL
  // The pipeline's levels arrive in 200 ms batches: two polls per batch keep each
  // poll to one batch, which the wave's short queue plays without skipping.
  return session === 'live' ? 2 * LEVEL_INTERVAL : 250
}
