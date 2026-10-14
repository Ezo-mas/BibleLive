import { useCallback, useEffect, useRef, useState } from 'react'
import {
  bibleVersionLabel,
  changeBibleVersion,
  fetchBibleVersion,
  parseBibleVersionInfo,
} from '../bibleVersionApi'
import type { BibleVersionInfo } from '../bibleVersionApi'

export interface BibleVersionState {
  /** The translation the server last confirmed, or null before the first answer. */
  version: string | null
  /** Translations the server can switch to (empty until the first answer). */
  available: string[]
  /** Why the last read or switch failed, for the dialog to show inline. */
  error: string | null
  /** True while a switch is in flight; the buttons wait for the answer. */
  isSwitching: boolean
  /** Read again (the dialog does this when it opens). */
  refresh: () => void
  /** Switch to `next`. Resolves into `version`, never optimistically. */
  change: (next: string) => void
  /**
   * Take a `bible_version_confirmed` WebSocket message: another operator tab, or
   * the automatic switch that follows a language change, moved the translation.
   */
  applyConfirmed: (data: unknown) => void
}

type Notify = (tone: 'info' | 'error', text: string) => void

function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : 'The Bible version could not be changed.'
}

/**
 * The Bible translation verse text comes from, read and switched through the server.
 *
 * Like the spoken language, it is server-owned: the console shows only what the
 * server confirmed, never an optimistic guess. Changes it did not ask for
 * (another operator, or the language-default switch) arrive over the socket and
 * are announced once.
 */
export function useBibleVersion(
  token: string | null,
  connected: boolean,
  notify: Notify
): BibleVersionState {
  const [info, setInfo] = useState<BibleVersionInfo>({ version: null, available: [] })
  const [error, setError] = useState<string | null>(null)
  const [isSwitching, setIsSwitching] = useState(false)

  const readSeqRef = useRef(0)
  // Counts confirmations pushed over the socket. A switch whose HTTP reply
  // arrives after a newer push must not overwrite it with its older answer.
  const pushSeqRef = useRef(0)
  const switchingRef = useRef(false)
  const versionRef = useRef<string | null>(null)
  versionRef.current = info.version
  const notifyRef = useRef(notify)
  notifyRef.current = notify

  const refresh = useCallback(() => {
    const seq = (readSeqRef.current += 1)
    fetchBibleVersion(token)
      .then(current => {
        if (seq !== readSeqRef.current) return
        setInfo(current)
        setError(null)
      })
      .catch((failure: unknown) => {
        if (seq !== readSeqRef.current) return
        setError(messageOf(failure))
      })
  }, [token])

  useEffect(() => {
    if (connected) refresh()
  }, [connected, refresh])

  useEffect(
    () => () => {
      readSeqRef.current += 1
    },
    []
  )

  const change = useCallback(
    (next: string) => {
      if (switchingRef.current || next === versionRef.current) return
      switchingRef.current = true
      setIsSwitching(true)
      setError(null)
      readSeqRef.current += 1
      const pushSeqAtStart = pushSeqRef.current
      changeBibleVersion(next, token)
        .then(confirmed => {
          // A push that landed meanwhile is at least as new as this reply.
          if (pushSeqRef.current === pushSeqAtStart) setInfo(confirmed)
          if (confirmed.version) {
            notifyRef.current('info', `Bible version: ${bibleVersionLabel(confirmed.version)}`)
          }
        })
        .catch((failure: unknown) => {
          const message = messageOf(failure)
          setError(message)
          notifyRef.current('error', message)
        })
        .finally(() => {
          switchingRef.current = false
          setIsSwitching(false)
        })
    },
    [token]
  )

  const applyConfirmed = useCallback((data: unknown) => {
    const confirmed = parseBibleVersionInfo(data)
    if (!confirmed) return
    // A read still in flight is older than this confirmation.
    readSeqRef.current += 1
    pushSeqRef.current += 1
    const changed = confirmed.version !== versionRef.current
    setInfo(confirmed)
    setError(null)
    // The switching operator's own request announces its result; this covers the rest.
    if (changed && confirmed.version && !switchingRef.current) {
      notifyRef.current('info', `Bible version: ${bibleVersionLabel(confirmed.version)}`)
    }
  }, [])

  return { ...info, error, isSwitching, refresh, change, applyConfirmed }
}
