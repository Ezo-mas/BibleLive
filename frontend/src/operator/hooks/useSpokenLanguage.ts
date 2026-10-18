import { useCallback, useEffect, useRef, useState } from 'react'
import {
  LanguageApiError,
  changeSpokenLanguage,
  fetchSpokenLanguage,
  spokenLanguageLabel,
} from '../languageApi'
import { useI18n } from '../../shared/i18nContext'
import type { Translator } from '../../shared/i18n'

/**
 * Where the language reading stands.
 *
 * `loading` is only the very first read; once a language is known it stays shown
 * while a refresh runs. `unavailable` means the server could not get an answer
 * from the audio pipeline (not running, or its control API is off), and says
 * nothing about which language it is in.
 */
export type SpokenLanguageStatus = 'loading' | 'ready' | 'unavailable'

export interface SpokenLanguageState {
  /** The language the pipeline last confirmed, or null before the first answer. */
  language: string | null
  status: SpokenLanguageStatus
  /** Why the last read or switch failed, for the dialog to show inline. */
  error: string | null
  /** True while a switch is in flight; the controls wait for the answer. */
  isSwitching: boolean
  /** Ask the pipeline again (the dialog does this when it opens). */
  refresh: () => void
  /** Switch the pipeline to `next`. Resolves into `language`, never optimistically. */
  change: (next: string) => void
}

type Notify = (tone: 'info' | 'error', text: string) => void

function messageOf(error: unknown, t: Translator): string {
  return error instanceof Error ? error.message : t('language.changeFailed')
}

/** Whether a failure means the pipeline itself is out of reach (not a refusal). */
function isUnreachable(error: unknown): boolean {
  return error instanceof LanguageApiError && (error.status === 0 || error.status >= 502)
}

/**
 * The audio pipeline's spoken language, read and changed through the server.
 *
 * The language belongs to the pipeline — a separate process that owns the
 * microphone — so this hook holds nothing it was not told: it reads on connect
 * and on demand, and a switch becomes the new value only when the pipeline
 * confirms it. There is no optimistic update, because showing "Lithuanian" while
 * the pipeline is still transcribing English is worse than a short wait.
 *
 * A read that fails leaves the console working: the language is simply unknown
 * and the controls say why. A reading in flight is discarded when a newer one or
 * a switch supersedes it, so a slow answer cannot overwrite a fresh one.
 */
export function useSpokenLanguage(
  token: string | null,
  connected: boolean,
  notify: Notify
): SpokenLanguageState {
  const [language, setLanguage] = useState<string | null>(null)
  const [status, setStatus] = useState<SpokenLanguageStatus>('loading')
  const [error, setError] = useState<string | null>(null)
  const [isSwitching, setIsSwitching] = useState(false)
  const { t } = useI18n()

  // Held in refs so `change` keeps a stable identity and the guards read the
  // current values rather than the ones from the render that created them. The
  // translator is held the same way, so a language switch does not re-read the
  // pipeline.
  const readSeqRef = useRef(0)
  const switchingRef = useRef(false)
  const languageRef = useRef<string | null>(null)
  languageRef.current = language
  const notifyRef = useRef(notify)
  notifyRef.current = notify
  const tRef = useRef(t)
  tRef.current = t

  const refresh = useCallback(() => {
    const seq = (readSeqRef.current += 1)
    fetchSpokenLanguage(token, tRef.current)
      .then(current => {
        if (seq !== readSeqRef.current) return
        setLanguage(current)
        setStatus('ready')
        setError(null)
      })
      .catch((failure: unknown) => {
        if (seq !== readSeqRef.current) return
        setStatus('unavailable')
        setError(messageOf(failure, tRef.current))
      })
  }, [token])

  // Read when the console connects, and again after a reconnect: the pipeline
  // may have been started or restarted while the socket was down.
  useEffect(() => {
    if (connected) refresh()
  }, [connected, refresh])

  // A late answer must not land on an unmounted console.
  useEffect(
    () => () => {
      readSeqRef.current += 1
    },
    []
  )

  const change = useCallback(
    (next: string) => {
      if (switchingRef.current || next === languageRef.current) return
      switchingRef.current = true
      setIsSwitching(true)
      setError(null)
      // The switch is newer than any read still in flight, so those are dropped.
      readSeqRef.current += 1
      changeSpokenLanguage(next, token, tRef.current)
        .then(confirmed => {
          setLanguage(confirmed)
          setStatus('ready')
          notifyRef.current(
            'info',
            tRef.current('language.changed', {
              language: spokenLanguageLabel(confirmed, tRef.current),
            })
          )
        })
        .catch((failure: unknown) => {
          const message = messageOf(failure, tRef.current)
          setError(message)
          if (isUnreachable(failure)) setStatus('unavailable')
          notifyRef.current('error', message)
        })
        .finally(() => {
          switchingRef.current = false
          setIsSwitching(false)
        })
    },
    [token]
  )

  return { language, status, error, isSwitching, refresh, change }
}
