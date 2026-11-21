/**
 * How long the projector and the stream overlay take to change what they show.
 *
 * Every swap is an exit followed by an entrance, never both at once (a crossfade puts
 * two passages on screen together, which is hard to read). The exit takes 40% of the
 * swap and the entrance 60%, so the old text clears quickly and the new one settles in
 * gently; the slide is split the same way (a short drift out, a longer drift in).
 *
 * The CSS reads the durations from `--exit-ms` / `--enter-ms`, set inline from these.
 */
export interface SwapTiming {
  exitMs: number
  enterMs: number
}

/** Passage to passage: 250 ms in all. */
export const VERSE_SWAP: SwapTiming = { exitMs: 100, enterMs: 150 }

/** Idle screen to passage and back (and the stream band coming and going): 400 ms in all. */
export const SCREEN_SWAP: SwapTiming = { exitMs: 160, enterMs: 240 }

/** The CSS custom properties that carry a swap's durations to its animations. */
export function swapStyle(timing: SwapTiming): Record<string, string> {
  return { '--exit-ms': `${timing.exitMs}ms`, '--enter-ms': `${timing.enterMs}ms` }
}
