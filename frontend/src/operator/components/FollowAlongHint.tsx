import { BookIcon, Button } from '../../components'
import type { FollowAlongHint as FollowAlongHintData } from '../../shared/types'

export interface FollowAlongHintProps {
  /** The verses the speaker seems to be on, or null when there is no suggestion. */
  hint: FollowAlongHintData | null
  /** False while the socket is closed or another Show is still waiting on the server. */
  canShow: boolean
  onShow: () => void
}

/** "Luke 16:13" for one verse, "Luke 16:13-15" for a run (same hyphen as the stages). */
function formatHint(hint: FollowAlongHintData): string {
  const range =
    hint.verse_end > hint.verse_start ? `${hint.verse_start}-${hint.verse_end}` : hint.verse_start
  return `${hint.book} ${hint.chapter}:${range}`
}

/**
 * Follow-along: a quiet suggestion that the speaker has read on past the passage
 * on screen, with a Show button for the verses that come next.
 *
 * It is a convenience, never a prompt. It is a single slim line under the two
 * stages, drawn as a plain row on the panel colour, with a secondary button — the
 * one filled button in the console stays Show on display — and it takes no keyboard
 * shortcut of its own, so a stray Enter can never put it on the projector. It
 * renders nothing without a hint, and pushes only the history list down when it
 * appears, never the stages or the queue.
 *
 * The button sends a normal manual `display_reference` for the hint's coordinates;
 * the server re-resolves them, so nothing here can show text the operator has not
 * chosen to show.
 */
export function FollowAlongHint({ hint, canShow, onShow }: FollowAlongHintProps) {
  if (!hint) return null

  const label = formatHint(hint)

  return (
    <section className="operator-follow" aria-label="Follow-along suggestion">
      <span className="operator-follow-icon" aria-hidden="true">
        <BookIcon />
      </span>
      <p className="operator-follow-text">
        <span className="operator-follow-label">Reading now</span>
        <strong className="operator-follow-reference">{label}</strong>
      </p>
      <Button
        variant="secondary"
        size="sm"
        className="operator-follow-show"
        onClick={onShow}
        disabled={!canShow}
        aria-label={`Show ${label} on display`}
      >
        Show
      </Button>
    </section>
  )
}
