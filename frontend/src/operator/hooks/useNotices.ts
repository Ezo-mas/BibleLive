import { useCallback, useEffect, useRef, useState } from 'react'
import type { MessageParams, Translator } from '../../shared/i18n'
import { useI18n } from '../../shared/i18nContext'
import type { NoticeMessage } from '../state/operatorTypes'

/** Which action button briefly pulses once the server confirms it. */
export type FlashTarget = 'approve' | 'clear' | 'clearPending'

/** How a notice reads: a confirmation, a neutral fact, or a failure. */
export type NoticeTone = 'success' | 'info' | 'error'

/** A single server-confirmed message for the operator. */
export interface OperatorNotice {
  /** Changes with every notice so repeating a message announces it again. */
  id: number
  tone: NoticeTone
  text: string
}

const FLASH_MS = 150
const NOTICE_MS: Record<NoticeTone, number> = { success: 3000, info: 4000, error: 6000 }

function translateNotice(message: NoticeMessage, t: Translator): string {
  const params: MessageParams = {}
  for (const [name, value] of Object.entries(message.params ?? {})) {
    params[name] =
      typeof value !== 'object'
        ? value
        : 'join' in value
          ? value.join.map(part => translateNotice(part, t)).join(', ')
          : translateNotice(value, t)
  }
  return t(message.key, params)
}

/** One replaceable notice slot and the server-confirmed button pulse. */
export function useNotices() {
  const { t } = useI18n()
  const [notice, setNotice] = useState<OperatorNotice | null>(null)
  const [flash, setFlash] = useState<FlashTarget | null>(null)
  const noticeIdRef = useRef(0)
  const flashTimerRef = useRef<number | undefined>(undefined)
  const noticeTimerRef = useRef<number | undefined>(undefined)

  const pulse = useCallback((target: FlashTarget) => {
    setFlash(target)
    window.clearTimeout(flashTimerRef.current)
    flashTimerRef.current = window.setTimeout(() => setFlash(null), FLASH_MS)
  }, [])

  const showNotice = useCallback((tone: NoticeTone, text: string) => {
    noticeIdRef.current += 1
    setNotice({ id: noticeIdRef.current, tone, text })
    window.clearTimeout(noticeTimerRef.current)
    noticeTimerRef.current = window.setTimeout(() => setNotice(null), NOTICE_MS[tone])
  }, [])

  const showMessage = useCallback(
    (tone: NoticeTone, message: NoticeMessage) => showNotice(tone, translateNotice(message, t)),
    [showNotice, t]
  )

  const dismissNotice = useCallback(() => {
    window.clearTimeout(noticeTimerRef.current)
    setNotice(null)
  }, [])

  useEffect(
    () => () => {
      window.clearTimeout(flashTimerRef.current)
      window.clearTimeout(noticeTimerRef.current)
    },
    []
  )

  return { notice, flash, showNotice, showMessage, pulse, dismissNotice }
}
