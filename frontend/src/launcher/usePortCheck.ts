import { useEffect, useRef, useState } from 'react'
import type { Bridge, PortCheck } from './bridge'

type PortResult = Exclude<PortCheck, { error: string }>
export type PortConflict = PortResult & { port: string }

const PORT_DEBOUNCE_MS = 400
const FOCUS_INTERVAL_MS = 3000

export function usePortCheck(bridge: Bridge, port: string, idle: boolean, networkShown: boolean) {
  const [result, setResult] = useState<PortConflict | null>(null)
  const previousPort = useRef(port)
  const lastCheck = useRef(-Infinity)
  const checkNow = useRef<(() => void) | null>(null)

  useEffect(() => {
    const portChanged = previousPort.current !== port
    previousPort.current = port
    if (!idle) return

    let active = true
    let latest = 0
    let timer: number | undefined
    setResult(null)

    async function check() {
      window.clearTimeout(timer)
      timer = undefined
      const request = ++latest
      lastCheck.current = performance.now()
      try {
        const answer = await bridge.check_port(port)
        if (active && request === latest) {
          setResult('free' in answer && !answer.free ? { ...answer, port } : null)
        }
      } catch {
        if (active && request === latest) setResult(null)
      }
    }

    function onFocus() {
      if (timer === undefined && performance.now() - lastCheck.current >= FOCUS_INTERVAL_MS) {
        void check()
      }
    }

    checkNow.current = () => void check()
    if (portChanged) timer = window.setTimeout(check, PORT_DEBOUNCE_MS)
    else void check()
    window.addEventListener('focus', onFocus)
    return () => {
      active = false
      checkNow.current = null
      window.clearTimeout(timer)
      window.removeEventListener('focus', onFocus)
    }
  }, [bridge, port, idle])

  useEffect(() => {
    if (networkShown) checkNow.current?.()
  }, [networkShown])

  return idle && result?.port === port ? result : null
}
