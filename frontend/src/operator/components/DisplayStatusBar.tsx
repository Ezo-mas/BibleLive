import { useId } from 'react'
import { ArrowUpIcon, Button } from '../../components'
import type { Translator } from '../../shared/i18n'
import { useI18n } from '../../shared/i18nContext'
import { formatReference } from '../../shared/reference'
import type { OperatorState } from '../state/operatorHookTypes'

type DisplayStatus = Pick<
  OperatorState,
  | 'connectionState'
  | 'displayMissing'
  | 'displayConnected'
  | 'currentDisplay'
  | 'seenPassage'
  | 'live'
>

function displaySummary(status: DisplayStatus, t: Translator) {
  if (status.connectionState === 'disconnected') return t('connection.disconnected')
  if (status.displayMissing) return t('stage.noDisplay')
  if (!status.displayConnected) return t('header.connecting')

  const air = t(status.live ? 'stage.liveOn' : 'stage.liveOff')
  if (status.currentDisplay) return `${air} · ${formatReference(status.currentDisplay)}`
  if (!status.live) return air
  return t(status.seenPassage ? 'stage.displayCleared' : 'stage.nothingOnDisplay')
}

export function DisplayStatusBar({
  status,
  onJump,
}: {
  status: DisplayStatus
  onJump: () => void
}) {
  const { t } = useI18n()
  const summaryId = useId()
  const summary = displaySummary(status, t)

  return (
    <>
      <Button
        type="button"
        className="operator-display-summary"
        variant="secondary"
        size="md"
        aria-label={t('stage.jumpToPreview')}
        aria-describedby={summaryId}
        onClick={onJump}
      >
        <span className="operator-display-summary-text" aria-hidden="true">
          {summary}
        </span>
        <ArrowUpIcon aria-hidden="true" />
      </Button>
      <span className="sr-only" role="status" id={summaryId}>
        {summary}
      </span>
    </>
  )
}
