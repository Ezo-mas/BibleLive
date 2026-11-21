import { LEVEL_INTERVAL } from '../../shared/levels'

const BAR_INTERVAL_MS = 200
const SAMPLES_PER_BAR = BAR_INTERVAL_MS / LEVEL_INTERVAL
const BAR_COUNT = 5
// DirectShow can deliver audio in 500 ms bursts. Allow room for capture and
// delivery jitter before dropping to the minimum.
const LEVEL_TIMEOUT_MS = 1500
const TOOLTIP_INTERVAL_MS = 1000
const EMPTY_BARS = Array<number>(BAR_COUNT).fill(0)

interface InputLevels {
  bars: number[]
  tooltip: { input: number; peak: number; at: number } | null
}

export type InputLevelsStore = ReturnType<typeof createInputLevelsStore>

export function createInputLevelsStore() {
  let snapshot: InputLevels | null = null
  const listeners = new Set<() => void>()
  const samples: number[] = []
  let waiting: number[] = []
  let barTimer: number | undefined
  let timeout: number | undefined

  function publish(next: InputLevels | null) {
    if (snapshot === next) return
    snapshot = next
    for (const listener of listeners) listener()
  }

  function clearBuffers() {
    window.clearTimeout(timeout)
    window.clearTimeout(barTimer)
    waiting = []
    samples.length = 0
  }

  function receive(levels: number[]) {
    if (!levels.length) return
    const now = Date.now()
    const input = levels[levels.length - 1]
    const peaks: number[] = []
    samples.push(...levels)
    while (samples.length >= SAMPLES_PER_BAR) {
      peaks.push(Math.max(...samples.splice(0, SAMPLES_PER_BAR)))
    }

    // Finish the previous batch on arrival, then pace the new batch from here.
    // Timer lateness can only affect the current batch.
    window.clearTimeout(barTimer)
    const ready = [...waiting, ...peaks.slice(0, 1)]
    waiting = peaks.slice(1)
    const previousBars = snapshot?.bars ?? EMPTY_BARS
    const bars = ready.length ? [...previousBars, ...ready].slice(-BAR_COUNT) : previousBars
    let tooltip = snapshot?.tooltip
    if (!tooltip || now - tooltip.at >= TOOLTIP_INTERVAL_MS) {
      tooltip = { input, peak: Math.max(...bars, ...samples, ...waiting), at: now }
    }
    if (snapshot?.bars !== bars || snapshot?.tooltip !== tooltip) publish({ bars, tooltip })

    function showNextBar() {
      const peak = waiting.shift()
      if (peak !== undefined && snapshot) {
        publish({ ...snapshot, bars: [...snapshot.bars.slice(1), peak] })
      }
      if (waiting.length) barTimer = window.setTimeout(showNextBar, BAR_INTERVAL_MS)
    }
    if (waiting.length) barTimer = window.setTimeout(showNextBar, BAR_INTERVAL_MS)

    window.clearTimeout(timeout)
    timeout = window.setTimeout(() => {
      clearBuffers()
      publish({ bars: EMPTY_BARS, tooltip: null })
    }, LEVEL_TIMEOUT_MS)
  }

  return {
    receive,
    reset() {
      clearBuffers()
      publish(null)
    },
    getSnapshot: () => snapshot,
    subscribe(listener: () => void) {
      listeners.add(listener)
      return () => listeners.delete(listener)
    },
  }
}
