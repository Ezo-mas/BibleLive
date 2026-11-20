const RELEASE_MS = 350
const SILENCE = 0.001

export function advanceEnvelope(level: number, peak: number, elapsed: number, meterOn: boolean) {
  if (!meterOn) return 0
  const next = Math.max(peak, level * 3 ** (-Math.max(0, elapsed) / RELEASE_MS))
  return next < SILENCE ? 0 : next
}
