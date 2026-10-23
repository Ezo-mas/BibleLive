import { useCallback, useRef, useState } from 'react'
import {
  changeIdleScreenFit,
  fetchIdleScreen,
  IDLE_SCREEN_MAX_BYTES,
  removeIdleScreen,
  uploadIdleScreen,
} from '../idleScreenApi'
import type { IdleScreenInfo } from '../idleScreenApi'
import { useI18n } from '../../shared/i18nContext'
import type { IdleScreenFit } from '../../shared/idleScreen'

export interface IdleScreenState {
  /** The server's answer, or null before the first one. */
  info: IdleScreenInfo | null
  /** Why the last read or change failed, for the settings dialog to show inline. */
  error: string | null
  /** True while a request is in flight; the controls wait for its answer. */
  isBusy: boolean
  /** Read again (the dialog does this when it opens). */
  refresh: () => void
  /** Store a picture. The server keeps the fit mode it has. */
  upload: (file: File) => void
  /** Choose the fit mode. Resolves into `info`, never optimistically. */
  changeFit: (fit: IdleScreenFit) => void
  /** Remove the picture. */
  remove: () => void
}

/**
 * The idle picture the display shows while nothing is on screen, read and changed
 * through the server. Nothing is read until `refresh` is called (the settings dialog does
 * so each time it opens), so the console makes no extra request on load. Requests run one
 * at a time: a request made while another is in flight is not sent, and the one in flight
 * answers for the setting, so a read can never land after a change it does not include.
 */
export function useIdleScreen(token: string | null): IdleScreenState {
  const [info, setInfo] = useState<IdleScreenInfo | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [isBusy, setIsBusy] = useState(false)
  const busyRef = useRef(false)
  const infoRef = useRef<IdleScreenInfo | null>(null)
  infoRef.current = info
  // The translator is held in a ref so a language switch does not re-read the setting.
  const { t } = useI18n()
  const tRef = useRef(t)
  tRef.current = t

  const run = useCallback((request: () => Promise<IdleScreenInfo>) => {
    if (busyRef.current) return
    busyRef.current = true
    setIsBusy(true)
    setError(null)
    request()
      .then(next => setInfo(next))
      .catch((failure: unknown) => {
        setError(failure instanceof Error ? failure.message : tRef.current('idle.unreachable'))
      })
      .finally(() => {
        busyRef.current = false
        setIsBusy(false)
      })
  }, [])

  const refresh = useCallback(() => {
    run(() => fetchIdleScreen(token, tRef.current))
  }, [run, token])

  const upload = useCallback(
    (file: File) => {
      if (file.size > IDLE_SCREEN_MAX_BYTES) {
        setError(
          tRef.current('idle.tooLarge', { megabytes: IDLE_SCREEN_MAX_BYTES / (1024 * 1024) })
        )
        return
      }
      run(() => uploadIdleScreen(file, token, tRef.current))
    },
    [run, token]
  )

  const changeFit = useCallback(
    (fit: IdleScreenFit) => {
      if (infoRef.current?.fit === fit) return
      run(() => changeIdleScreenFit(fit, token, tRef.current))
    },
    [run, token]
  )

  const remove = useCallback(() => {
    run(() => removeIdleScreen(token, tRef.current))
  }, [run, token])

  return { info, error, isBusy, refresh, upload, changeFit, remove }
}
