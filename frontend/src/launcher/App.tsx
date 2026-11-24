import { useEffect, useLayoutEffect, useReducer, useRef, useState } from 'react'
import { useI18n } from '../shared/i18nContext'
import { getBridge } from './bridge'
import type {
  Bridge,
  Form,
  InitResult,
  LogLine,
  Meter,
  StartError,
  UILanguage,
  View,
} from './bridge'
import type { LogSourceFilter } from './consoleFilter'
import { Console } from './Console'
import { isDemoBridge } from './demoBridge'
import { Icon } from './Icon'
import { LauncherI18nProvider } from './LauncherI18nProvider'
import { initialData, launcherReducer } from './launcherReducer'
import { pollInterval } from './pollInterval'
import { Hero, Setup } from './Session'
import { Settings } from './Settings'
import type { SettingsTab } from './Settings'
import { useLevelEnvelope } from './useLevelEnvelope'
import { usePortCheck } from './usePortCheck'

interface Connection {
  bridge: Bridge
  initial: InitResult
}

const BRIDGE_WAIT_MS = 15_000

export function App({ bridge }: { bridge?: Bridge }) {
  const { t } = useI18n()
  const [connection, setConnection] = useState<Connection | null>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    let active = true
    setFailed(false)
    const timeout = bridge ? undefined : window.setTimeout(() => setFailed(true), BRIDGE_WAIT_MS)
    async function connect() {
      try {
        const api = bridge ?? (await getBridge())
        window.clearTimeout(timeout)
        if (!active) return
        const initial = await api.init()
        if (active) setConnection({ bridge: api, initial })
      } catch {
        if (active) setFailed(true)
      }
    }
    void connect()
    return () => {
      active = false
      window.clearTimeout(timeout)
    }
  }, [bridge])

  if (!connection)
    return (
      <p className="bl-loading" role="status">
        {t(failed ? 'launcher.bridgeError' : 'launcher.loading')}
      </p>
    )
  return <Launcher {...connection} />
}

function Launcher({ bridge, initial }: Connection) {
  const [language, setLanguage] = useState(initial.language)
  const [form, setForm] = useState(initial.form)
  const [view, setView] = useState(initial.view)
  const [tab, setTab] = useState<SettingsTab>(initial.tab)
  const [data, dispatch] = useReducer(launcherReducer, initial, initialData)
  const [pending, setPending] = useState<'start' | 'stop' | null>(null)
  const [selfTestPending, setSelfTestPending] = useState(false)
  const [error, setError] = useState<StartError | null>(null)
  const [pollFailed, setPollFailed] = useState(false)
  const [actionFailed, setActionFailed] = useState(false)
  const [focus, setFocus] = useState<'port' | 'extra_args' | 'console' | null>(null)
  const portRef = useRef<HTMLInputElement>(null)
  const argsRef = useRef<HTMLInputElement>(null)
  const logRef = useRef<HTMLDivElement>(null)
  const rootRef = useRef<HTMLElement>(null)
  const languageRequest = useRef(Promise.resolve())
  const microphoneRequest = useRef({ pending: false, done: Promise.resolve() })
  const sessionRequest = useRef<{ readyForPoll: boolean } | null>(null)
  const selfTestRequest = useRef<{ readyForPoll: boolean } | null>(null)
  const pollNow = useRef<(() => void) | null>(null)

  const { state } = data
  const locked = state.session !== 'idle' || pending !== null
  const canProject = state.session === 'live' && pending !== 'stop'
  const checkedPort = usePortCheck(
    bridge,
    form.port,
    !locked,
    view === 'advanced' && tab === 'network'
  )
  // A failed Start already names the program on this port: don't say it twice.
  const portConflict =
    checkedPort && state.error?.port === Number(checkedPort.port) ? null : checkedPort
  const { waveRef, meterRef, acceptLevels } = useLevelEnvelope(
    !locked && data.meter === 'on'
      ? 'microphone'
      : state.session === 'live' && pending === null
        ? 'pipeline'
        : null
  )

  useLayoutEffect(() => {
    const frame = window.requestAnimationFrame(() => {
      const root = rootRef.current
      if (root) void bridge.fit_window(root.scrollHeight, window.innerHeight).catch(() => {})
    })
    return () => window.cancelAnimationFrame(frame)
  }, [bridge, view])

  useEffect(() => {
    let active = true
    let timer: number
    let polling = false
    let pollAgain = false
    let session = initial.state.session
    let meter: Meter = 'off'

    function delay() {
      return pollInterval(sessionRequest.current ? 'starting' : session, meter, document.hidden)
    }

    function requestPoll() {
      window.clearTimeout(timer)
      if (polling) pollAgain = true
      else void poll()
    }

    function visibilityChanged() {
      if (!document.hidden) {
        requestPoll()
      } else {
        acceptLevels(null)
        window.clearTimeout(timer)
        if (!polling) timer = window.setTimeout(poll, delay())
      }
    }

    async function poll() {
      polling = true
      const started = performance.now()
      const visible = !document.hidden
      const request = sessionRequest.current
      const releasePending = request?.readyForPoll
      const testRequest = selfTestRequest.current
      const releaseSelfTest = testRequest?.readyForPoll
      const microphone = microphoneRequest.current
      const microphoneReady = !microphone.pending
      try {
        const result = await bridge.poll()
        if (active) {
          session = result.state.session
          meter = result.meter
          // A poll that started before the microphone switch may still return old samples.
          const levels =
            microphoneReady && microphone === microphoneRequest.current ? result.levels : []
          acceptLevels(
            visible &&
              !document.hidden &&
              ((meter === 'on' && session === 'idle') || session === 'live') &&
              (!request || releasePending)
              ? levels
              : null
          )
          dispatch({ type: 'poll', result: { ...result, levels }, now: Date.now() })
          setPollFailed(false)
          if (releasePending && sessionRequest.current === request) {
            sessionRequest.current = null
            setPending(null)
          }
          if (releaseSelfTest && selfTestRequest.current === testRequest) {
            selfTestRequest.current = null
            setSelfTestPending(false)
          }
        }
      } catch {
        if (active) {
          meter = 'off'
          acceptLevels(null)
          setPollFailed(true)
        }
      } finally {
        polling = false
        if (active) {
          // Coalesce immediate requests made while the bridge was busy.
          const wait = pollAgain ? 0 : Math.max(0, delay() - (performance.now() - started))
          pollAgain = false
          timer = window.setTimeout(poll, wait)
        }
      }
    }
    pollNow.current = requestPoll
    document.addEventListener('visibilitychange', visibilityChanged)
    void poll()
    return () => {
      active = false
      pollNow.current = null
      document.removeEventListener('visibilitychange', visibilityChanged)
      window.clearTimeout(timer)
    }
  }, [acceptLevels, bridge, initial.state.session])

  useLayoutEffect(() => {
    if (focus === 'port') portRef.current?.focus()
    if (focus === 'extra_args') {
      const input = argsRef.current
      const details = input?.closest('details')
      if (details) details.open = true
      input?.focus()
    }
    if (focus === 'console') logRef.current?.focus()
    if (focus) setFocus(null)
  }, [focus])

  async function runAction(action: () => Promise<unknown>) {
    setActionFailed(false)
    try {
      await action()
    } catch {
      setActionFailed(true)
    } finally {
      pollNow.current?.()
    }
  }

  function chooseTab(next: SettingsTab) {
    setTab(next)
    void bridge.set_advanced_tab(next).catch(() => {})
  }

  function changeFilters(source: LogSourceFilter, level: LogLine['level']) {
    void bridge.set_console_filters(source, level).catch(() => {})
  }

  function chooseView(next: View) {
    if (next === view) return
    setView(next)
    void runAction(() => bridge.set_view(next))
  }

  function change<K extends keyof Form>(key: K, value: Form[K]) {
    setForm(current => ({ ...current, [key]: value }))
    setError(current => (current?.field === key ? null : current))
  }

  function changeLanguage(code: UILanguage) {
    change('ui_language', code)
    languageRequest.current = languageRequest.current.then(() =>
      runAction(async () => {
        const translated = await bridge.set_ui_language(code)
        setLanguage(translated.language)
        dispatch({ type: 'options', options: translated.options })
      })
    )
  }

  function changeMicrophone(device: Form['input_device']) {
    change('input_device', device)
    acceptLevels(null)
    dispatch({ type: 'resetLevels' })
    const previous = microphoneRequest.current
    const request = { pending: true, done: Promise.resolve() }
    microphoneRequest.current = request
    request.done = previous.done.then(async () => {
      if (microphoneRequest.current !== request) return
      await runAction(async () => {
        try {
          await bridge.set_microphone(device)
        } finally {
          request.pending = false
        }
      })
    })
  }

  async function toggleSession() {
    const stopping = state.session === 'live' || state.session === 'starting'
    if (pending === 'stop' || (!stopping && (pending || state.session !== 'idle'))) return

    const request = { readyForPoll: false }
    sessionRequest.current = request
    setPending(stopping ? 'stop' : 'start')
    await runAction(async () => {
      if (stopping) {
        await bridge.stop()
        request.readyForPoll = true
        return
      }
      setError(null)
      const result = await bridge.start(form)
      if (result.ok) {
        request.readyForPoll = true
      } else if (sessionRequest.current === request) {
        sessionRequest.current = null
        setPending(null)
        setError(result.error)
        const field = result.error.field
        if (field === 'port' || field === 'extra_args') {
          chooseView('advanced')
          setTab(field === 'port' ? 'network' : 'speech')
          setFocus(field)
        }
      }
    })
    if (!request.readyForPoll && sessionRequest.current === request) {
      sessionRequest.current = null
      setPending(null)
    }
  }

  async function runSelfTest(): Promise<string | null> {
    if (selfTestRequest.current) return null
    const request = { readyForPoll: false }
    selfTestRequest.current = request
    setSelfTestPending(true)
    let error: string | null = null
    await runAction(async () => {
      const result = await bridge.run_self_test(form)
      if (result.ok) request.readyForPoll = true
      else error = result.error.text
    })
    if (!request.readyForPoll) {
      selfTestRequest.current = null
      setSelfTestPending(false)
    }
    return error
  }

  return (
    <LauncherI18nProvider language={language}>
      <LauncherFrame
        rootRef={rootRef}
        view={view}
        session={state.session}
        chooseView={chooseView}
        preview={isDemoBridge(bridge)}
        bridgeFailed={pollFailed || actionFailed}
      >
        <Hero
          waveRef={waveRef}
          data={data}
          pending={pending}
          stopped={data.sessionStopped}
          error={error?.field === null ? error.text : undefined}
          portConflict={portConflict}
          onSession={() => void toggleSession()}
          onOperator={() => void runAction(() => bridge.open_operator())}
          onDetails={() => {
            chooseView('advanced')
            if (state.error?.port != null) {
              setTab('network')
              setFocus('port')
            } else {
              setFocus('console')
            }
          }}
        />
        <Setup
          meterRef={meterRef}
          data={data}
          form={form}
          locked={locked}
          canProject={canProject}
          error={error}
          onChange={change}
          onMicrophone={changeMicrophone}
          onRefresh={() => void runAction(() => bridge.refresh_devices())}
          onProjector={() => void runAction(() => bridge.show_projector())}
          onIdentify={() => void runAction(() => bridge.identify_screens())}
          audienceQr={
            state.network?.display_url
              ? {
                  shown: state.network.audience_qr,
                  busy: state.network.audience_qr_busy,
                  onToggle: () => {
                    const shown = state.network?.audience_qr ?? false
                    void runAction(() => bridge.set_audience_qr(!shown))
                  },
                }
              : undefined
          }
        />
        <div className="bl-workspace" hidden={view !== 'advanced'}>
          <Settings
            visible={view === 'advanced'}
            tab={tab}
            onTab={chooseTab}
            form={form}
            options={data.options}
            locked={locked}
            platform={initial.platform}
            network={state.network}
            error={error}
            portConflict={portConflict}
            portRef={portRef}
            argsRef={argsRef}
            onChange={change}
            onLanguage={changeLanguage}
            bridge={bridge}
            selfTest={state.self_test}
            selfTestPending={selfTestPending}
            onSelfTest={runSelfTest}
            onFirewall={() => void runAction(() => bridge.allow_firewall())}
          />
          <Console
            data={data}
            logRef={logRef}
            visible={view === 'advanced'}
            initialFilters={initial.console}
            onFilters={changeFilters}
            bridge={bridge}
            onLogs={() => void runAction(() => bridge.open_logs())}
            onDiagnostics={() => void runAction(() => bridge.collect_diagnostics())}
          />
        </div>
      </LauncherFrame>
    </LauncherI18nProvider>
  )
}

function LauncherFrame({
  rootRef,
  view,
  session,
  chooseView,
  preview,
  bridgeFailed,
  children,
}: {
  rootRef: React.RefObject<HTMLElement>
  view: View
  session: InitResult['state']['session']
  chooseView: (view: View) => void
  preview: boolean
  bridgeFailed: boolean
  children: React.ReactNode
}) {
  const { t } = useI18n()
  return (
    <main ref={rootRef} id="bl-launcher" data-mode={view} data-status={session}>
      <header className="bl-header">
        <div className="bl-identity">
          <span className="bl-mark">
            <Icon name="book" />
          </span>
          <strong>{t('launcher.brand')}</strong>
        </div>
        <div className="bl-switch" role="group" aria-label={t('launcher.view')}>
          {(['simple', 'advanced'] as const).map(name => (
            <button
              type="button"
              key={name}
              aria-pressed={view === name}
              onClick={() => chooseView(name)}
            >
              {t(`launcher.view.${name}`)}
            </button>
          ))}
        </div>
      </header>
      <div className="bl-body">
        {children}
        {(preview || bridgeFailed) && (
          <footer className="bl-footer">
            {preview && <span>{t('launcher.preview')}</span>}
            {bridgeFailed && (
              <span className="bl-error" role="status">
                {t('launcher.bridgeError')}
              </span>
            )}
          </footer>
        )}
      </div>
    </main>
  )
}
