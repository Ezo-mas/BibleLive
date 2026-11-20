import { useEffect, useLayoutEffect, useReducer, useRef, useState } from 'react'
import { useI18n } from '../shared/i18nContext'
import { getBridge } from './bridge'
import type { Bridge, Form, InitResult, StartError, UILanguage, View } from './bridge'
import { Console } from './Console'
import { isDemoBridge } from './demoBridge'
import { Icon } from './Icon'
import { LauncherI18nProvider } from './LauncherI18nProvider'
import { initialData, launcherReducer } from './launcherReducer'
import { LEVEL_INTERVAL } from './noSignal'
import { Hero, Setup } from './Session'
import { Settings } from './Settings'
import type { SettingsTab } from './Settings'
import { useLevelEnvelope } from './useLevelEnvelope'

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
  const [tab, setTab] = useState<SettingsTab>('speech')
  const [data, dispatch] = useReducer(launcherReducer, initial, initialData)
  const [pending, setPending] = useState<'start' | 'stop' | null>(null)
  const [error, setError] = useState<StartError | null>(null)
  const [pollFailed, setPollFailed] = useState(false)
  const [actionFailed, setActionFailed] = useState(false)
  const [focus, setFocus] = useState<'port' | 'extra_args' | 'console' | null>(null)
  const portRef = useRef<HTMLInputElement>(null)
  const argsRef = useRef<HTMLInputElement>(null)
  const logRef = useRef<HTMLDivElement>(null)
  const rootRef = useRef<HTMLElement>(null)
  const languageRequest = useRef(Promise.resolve())
  const sessionRequest = useRef<{ readyForPoll: boolean } | null>(null)

  const { state } = data
  const locked = state.session !== 'idle' || pending !== null
  const canProject = state.session === 'live' && pending !== 'stop'
  const { waveRef, meterRef, acceptLevels } = useLevelEnvelope(data.meter === 'on' && !locked)

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
    async function poll() {
      const started = performance.now()
      const request = sessionRequest.current
      const releasePending = request?.readyForPoll
      try {
        const result = await bridge.poll()
        if (active) {
          acceptLevels(
            result.meter === 'on' && result.state.session === 'idle' && (!request || releasePending)
              ? result.levels
              : null
          )
          dispatch({ type: 'poll', result, now: Date.now() })
          setPollFailed(false)
          if (releasePending && sessionRequest.current === request) {
            sessionRequest.current = null
            setPending(null)
          }
        }
      } catch {
        if (active) setPollFailed(true)
      } finally {
        if (active)
          timer = window.setTimeout(
            poll,
            Math.max(0, LEVEL_INTERVAL - (performance.now() - started))
          )
      }
    }
    void poll()
    return () => {
      active = false
      window.clearTimeout(timer)
    }
  }, [acceptLevels, bridge])

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
    }
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
          onSession={() => void toggleSession()}
          onOperator={() => void runAction(() => bridge.open_operator())}
          onDetails={() => {
            chooseView('advanced')
            setFocus('console')
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
          onMicrophone={device => {
            change('input_device', device)
            dispatch({ type: 'resetLevels' })
            void runAction(() => bridge.set_microphone(device))
          }}
          onRefresh={() => void runAction(() => bridge.refresh_devices())}
          onProjector={() => void runAction(() => bridge.show_projector())}
        />
        <div className="bl-workspace" hidden={view !== 'advanced'}>
          <Settings
            tab={tab}
            onTab={setTab}
            form={form}
            options={data.options}
            locked={locked}
            platform={initial.platform}
            network={state.network}
            error={error}
            portRef={portRef}
            argsRef={argsRef}
            onChange={change}
            onLanguage={changeLanguage}
            bridge={bridge}
            onFirewall={() => void runAction(() => bridge.allow_firewall())}
          />
          <Console
            data={data}
            logRef={logRef}
            visible={view === 'advanced'}
            onLogs={() => void runAction(() => bridge.open_logs())}
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
          <div>
            <strong>{t('launcher.brand')}</strong>
            <span>{t('launcher.name')}</span>
          </div>
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
