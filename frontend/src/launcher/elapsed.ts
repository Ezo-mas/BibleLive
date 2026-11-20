export function formatElapsed(startedAt: number, now: number): string {
  const seconds = Math.floor(Math.max(0, now - startedAt) / 1_000)
  return [Math.floor(seconds / 3_600), Math.floor(seconds / 60) % 60, seconds % 60]
    .map(part => String(part).padStart(2, '0'))
    .join(':')
}
