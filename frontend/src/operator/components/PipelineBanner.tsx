import { AlertCircleIcon } from '../../components'
import { hasMessage } from '../../shared/i18n'
import type { Translator } from '../../shared/i18n'
import { useI18n } from '../../shared/i18nContext'
import type { PipelineIssue, PipelineStatus } from '../../shared/types'

export interface PipelineBannerProps {
  status: PipelineStatus | null
}

/**
 * Warns that speech detection has stopped or is failing.
 *
 * An empty queue looks the same whether the preacher has not quoted anything or
 * the microphone was unplugged ten minutes ago, so the console says which it is.
 * Nothing renders while the pipeline is healthy, or when this server has never
 * heard from one (a console used only for manual passages). Manual search and
 * display keep working either way, and the banner says so.
 */
export function PipelineBanner({ status }: PipelineBannerProps) {
  const { t } = useI18n()
  if (status === null || (status.status !== 'offline' && status.status !== 'degraded')) {
    return null
  }

  // Structured issues are translated by code; a server without them, or a code this
  // console has no translation for, falls back to the sentence the server sent.
  const issues = status.issues ?? []

  return (
    <div className="operator-pipeline-banner" role="alert">
      <span className="operator-pipeline-banner-icon" aria-hidden="true">
        <AlertCircleIcon />
      </span>
      <div className="operator-pipeline-banner-text">
        {status.status === 'offline' ? (
          <p>
            <strong>{t('pipeline.offlineTitle')}</strong>{' '}
            {status.seconds_since_report !== null
              ? t('pipeline.offlineBodyAge', { age: formatAge(status.seconds_since_report, t) })
              : t('pipeline.offlineBody')}
          </p>
        ) : (
          <>
            <p>
              <strong>{t('pipeline.degradedTitle')}</strong> {t('pipeline.degradedBody')}
            </p>
            <ul>
              {issues.length > 0
                ? issues.map((issue, index) => (
                    // A code can repeat (one parser_failed per language): index keeps keys unique.
                    <li key={`${index}-${issue.code}`}>{issueText(issue, t)}</li>
                  ))
                : status.problems.map(problem => <li key={problem}>{problem}</li>)}
            </ul>
          </>
        )}
      </div>
    </div>
  )
}

/** The console's wording for one issue, or the server's own sentence when there is none. */
function issueText(issue: PipelineIssue, t: Translator): string {
  const key = `pipeline.issue.${issue.code}`
  if (!hasMessage(key)) return issue.text
  const params = { ...issue.params }
  // The thread's role is a word too, so it is translated before it is put in the sentence.
  if (issue.code === 'thread_stopped' && typeof params.role === 'string') {
    const roleKey = `pipeline.role.${params.role}`
    if (hasMessage(roleKey)) params.role = t(roleKey)
  }
  return t(key, params)
}

function formatAge(seconds: number, t: Translator): string {
  if (seconds < 90) return t('pipeline.ageSeconds', { seconds: Math.round(seconds) })
  return t('pipeline.ageMinutes', { minutes: Math.round(seconds / 60) })
}
