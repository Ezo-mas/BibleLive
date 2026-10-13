import { AlertCircleIcon } from '../../components'
import type { PipelineStatus } from '../../shared/types'

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
  if (status === null || (status.status !== 'offline' && status.status !== 'degraded')) {
    return null
  }

  return (
    <div className="operator-pipeline-banner" role="alert">
      <span className="operator-pipeline-banner-icon" aria-hidden="true">
        <AlertCircleIcon />
      </span>
      <div className="operator-pipeline-banner-text">
        {status.status === 'offline' ? (
          <p>
            <strong>Speech detection has stopped.</strong> The audio pipeline is not reporting
            {status.seconds_since_report !== null &&
              ` (last heard ${formatAge(status.seconds_since_report)} ago)`}
            , so no new references will arrive. Find Scripture still works.
          </p>
        ) : (
          <>
            <p>
              <strong>Speech detection has a problem.</strong> References may be missed until it is
              fixed. Find Scripture still works.
            </p>
            <ul>
              {status.problems.map(problem => (
                <li key={problem}>{problem}</li>
              ))}
            </ul>
          </>
        )}
      </div>
    </div>
  )
}

function formatAge(seconds: number): string {
  if (seconds < 90) return `${Math.round(seconds)} s`
  return `${Math.round(seconds / 60)} min`
}
