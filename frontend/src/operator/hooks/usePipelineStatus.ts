import { useEffect, useState } from 'react'
import type { PipelineStatus } from '../../shared/types'
import { authHeaders } from '../apiClient'

/** How often the console asks the server about the audio pipeline. */
export const PIPELINE_POLL_MS = 5000

/** The pipeline status, or null when the server could not be asked. */
async function fetchPipelineStatus(token: string | null): Promise<PipelineStatus | null> {
  try {
    const response = await fetch('/api/pipeline/status', { headers: authHeaders(token) })
    if (!response.ok) return null
    return (await response.json()) as PipelineStatus
  } catch {
    return null
  }
}

/**
 * Poll the server for the audio pipeline's health while `enabled`.
 *
 * Only runs while the operator socket is open: with no server connection the
 * connection banner already says everything that is true, and a pipeline
 * warning on top of it would only be a guess. A failed poll is not reported as
 * a pipeline problem for the same reason; the last answer simply stays until
 * the next one. Disabling clears the status so a stale warning cannot outlive
 * the session that fetched it.
 */
export function usePipelineStatus(token: string | null, enabled: boolean): PipelineStatus | null {
  const [status, setStatus] = useState<PipelineStatus | null>(null)

  useEffect(() => {
    if (!enabled) {
      setStatus(null)
      return
    }

    let cancelled = false
    let timer: number | undefined

    const poll = async () => {
      const next = await fetchPipelineStatus(token)
      if (cancelled) return
      if (next) setStatus(next)
      timer = window.setTimeout(poll, PIPELINE_POLL_MS)
    }
    void poll()

    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [token, enabled])

  return status
}
