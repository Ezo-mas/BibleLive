import { AlertCircleIcon, CheckIcon, InfoIcon, XIcon } from '../../components'
import { useI18n } from '../../shared/i18nContext'
import type { OperatorNotice } from '../hooks/useOperatorState'

export interface FeedbackNoticeProps {
  /** The single current notice, or null when there is nothing to say. */
  notice: OperatorNotice | null
  onDismiss: () => void
}

const TONE_ICONS = {
  success: <CheckIcon />,
  info: <InfoIcon />,
  error: <AlertCircleIcon />,
}

/**
 * The operator's action feedback: one message at a time, for confirmations and
 * failures that the screen itself cannot show.
 *
 * Every message here comes from the server, never from the click that sent the
 * request, so nothing reads as done before it is. Errors are assertive
 * (`role="alert"`) while confirmations stay polite, and the element is keyed by
 * the notice id so even a repeated message ("Shown: John 1:1-3" twice) is
 * announced again instead of looking unchanged.
 *
 * `OperatorHeader` renders it, between the title and the controls; the live
 * region follows the message wherever it sits.
 */
export function FeedbackNotice({ notice, onDismiss }: FeedbackNoticeProps) {
  const { t } = useI18n()
  if (!notice) return null

  return (
    <div
      key={notice.id}
      className={`operator-feedback operator-feedback-${notice.tone}`}
      role={notice.tone === 'error' ? 'alert' : 'status'}
      aria-live={notice.tone === 'error' ? 'assertive' : 'polite'}
    >
      <span className="operator-feedback-icon" aria-hidden="true">
        {TONE_ICONS[notice.tone]}
      </span>
      <span className="operator-feedback-text">{notice.text}</span>
      <button
        type="button"
        className="operator-feedback-dismiss"
        onClick={onDismiss}
        aria-label={t('app.dismissNotice')}
      >
        <XIcon />
      </button>
    </div>
  )
}
