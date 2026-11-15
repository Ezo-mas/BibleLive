import type { ClearMode } from './types'

/** Missing or invalid settings keep the existing idle-picture behaviour. */
export function coerceClearMode(value: unknown): ClearMode {
  return value === 'blank' ? 'blank' : 'idle'
}
