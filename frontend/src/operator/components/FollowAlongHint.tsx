import { Button } from '../../components'
import { useI18n } from '../../shared/i18nContext'
import { bookDisplayName } from '../../shared/reference'
import type { FollowAlongHint as FollowAlongHintData } from '../../shared/types'

export interface FollowAlongHintProps {
  /** The verses the speaker seems to be on, or null when there is no suggestion. */
  hint: FollowAlongHintData | null
  /** The start of the passage the hint points at, once looked up; null until then. */
  text?: string | null
  /** False while the socket is closed or another Show is still waiting on the server. */
  canShow: boolean
  onShow: () => void
}

/** "Luke 16:13" for one verse, "Luke 16:13-15" for a run (same hyphen as the stages). */
function formatHint(hint: FollowAlongHintData): string {
  const range =
    hint.verse_end > hint.verse_start ? `${hint.verse_start}-${hint.verse_end}` : hint.verse_start
  return `${bookDisplayName(hint)} ${hint.chapter}:${range}`
}

/**
 * Follow-along: a quiet suggestion that the speaker has read on past the passage
 * on screen, with a Show button for the verses that come next.
 *
 * It is a convenience, never a prompt. It is a card under Up Next with the same
 * anatomy (label, reference, Show at the right, a line or two of the verse below),
 * tinted with the accent so it reads as a suggestion, and it takes no keyboard
 * shortcut of its own, so a stray Enter can never put it on the projector. It
 * renders nothing without a hint, and pushes only the history list down when it
 * appears, never the stages or the queue.
 *
 * The button sends a normal manual `display_reference` for the hint's coordinates;
 * the server re-resolves them, so nothing here can show text the operator has not
 * chosen to show.
 */
export function FollowAlongHint({ hint, text, canShow, onShow }: FollowAlongHintProps) {
  const { t } = useI18n()
  if (!hint) return null

  const label = formatHint(hint)

  return (
    <section className="operator-follow operator-card" aria-label={t('follow.aria')}>
      <div className="operator-card-head">
        <div className="operator-card-title">
          <span className="operator-stage-title">{t('follow.label')}</span>
          <strong className="operator-card-ref">{label}</strong>
        </div>
        <span className="operator-card-grow" />
        <Button
          variant="primary"
          onClick={onShow}
          disabled={!canShow}
          aria-label={t('follow.showAria', { label })}
        >
          {t('follow.show')}
        </Button>
      </div>
      {/* The words come from a lookup; until they arrive the card is the reference alone. */}
      {text && (
        <div className="operator-card-body">
          <p className="operator-card-verse operator-follow-verse">{text}</p>
        </div>
      )}
    </section>
  )
}
