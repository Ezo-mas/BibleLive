import { translatorFor } from '../shared/i18n'
import type { Language } from '../shared/i18n'
import type { Bridge, Form, LogLine, Option, Options, State } from './bridge'
import { LEVEL_INTERVAL } from '../shared/levels'
import type { LogSourceFilter } from './consoleFilter'
import type { SettingsTab } from './Settings'

// A stand-in picture for the preview: the real code is drawn by the Python side.
const DEMO_QR =
  'data:image/svg+xml,' +
  encodeURIComponent(
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 9 9" shape-rendering="crispEdges">' +
      '<rect width="9" height="9" fill="#fff"/><path d="M1 1h3v3H1zm4 0h3v3H5zM1 5h3v3H1zm4 0h1v1H5zm2 2h1v1H7z"/></svg>'
  )

const idle: State = {
  session: 'idle',
  server: 'stopped',
  audio: 'stopped',
  speech: null,
  speech_warning: false,
  speech_fallback: false,
  self_test: { status: 'idle', text: null },
  started_at: null,
  has_monitors: true,
  network: null,
  error: null,
  diagnostics: { status: 'idle', file: null, text: null },
}

const defaults: Form = {
  language: 'en',
  input_device: null,
  projector_monitor: null,
  auto_mode: 'off',
  ui_language: 'auto',
  model: 'large-v3-turbo',
  device: 'auto',
  compute_type: 'auto',
  port: '8000',
  log_level: 'INFO',
  extra_args: '',
  open_display: true,
  open_operator: true,
  speech_engine: 'auto',
  allow_network: false,
  session_audio: false,
}

function demoOptions(language: Language): Options {
  const t = translatorFor(language)
  const plain = (...values: string[]): Option[] => values.map(value => ({ value, label: value }))
  return {
    language: [
      { value: 'en', label: t('launcher.demo.english') },
      { value: 'lt', label: t('launcher.demo.lithuanian') },
    ],
    microphone: [
      { value: null, label: t('launcher.demo.system') },
      { value: 'usb', label: t('launcher.demo.usb') },
      { value: 'built-in', label: t('launcher.demo.builtIn') },
    ],
    projector: [
      { value: null, label: t('launcher.demo.secondScreen') },
      { value: 'extended', label: t('launcher.demo.extended') },
      { value: 'main', label: t('launcher.demo.main') },
    ],
    speech_engine: [
      { value: 'auto', label: t('launcher.demo.recommended') },
      ...plain('Kmynas', 'Kmynas + Canary', 'Whisper').map(option => ({
        ...option,
        value: option.label.toLowerCase().replace(/ /g, ''),
      })),
    ],
    model: [
      { value: 'large-v3-turbo', label: t('launcher.demo.defaultModel') },
      ...plain('tiny', 'base', 'small', 'medium', 'large-v2', 'large-v3', 'distil-large-v3'),
    ],
    device: [{ value: 'auto', label: t('launcher.demo.automatic') }, ...plain('cuda', 'cpu')],
    compute_type: [
      { value: 'auto', label: t('launcher.demo.automatic') },
      ...plain('float16', 'float32', 'int8', 'int8_float16'),
    ],
    auto_mode: (['off', 'high', 'medium_and_up', 'all'] as const).map(value => ({
      value,
      label: t(`launcher.demo.auto.${value}`),
    })),
    ui_language: [
      { value: 'auto', label: t('launcher.demo.system') },
      { value: 'en', label: 'English' },
      { value: 'lt', label: 'Lietuvių' },
    ],
    log_level: plain('DEBUG', 'INFO', 'WARNING', 'ERROR'),
  }
}

const previews = new WeakSet<Bridge>()

export function isDemoBridge(bridge: Bridge) {
  return previews.has(bridge)
}

export function createDemoBridge(): Bridge {
  const clock = new Intl.DateTimeFormat('en-GB', { timeStyle: 'medium', hourCycle: 'h23' })
  let language: Language = navigator.language.toLowerCase().startsWith('lt') ? 'lt' : 'en'
  let view: 'simple' | 'advanced' = 'simple'
  let tab: SettingsTab = 'speech'
  let consoleFilters: { source: LogSourceFilter; level: LogLine['level'] } = {
    source: 'all',
    level: 'DEBUG',
  }
  let form = { ...defaults }
  let state = idle
  let lastLevelAt = Date.now() - LEVEL_INTERVAL
  let speechBlock = 0
  const syllables = [0.35, 0.82, 0.45, 0.04, 0.25, 0.65, 0.4, 0.04, 0.04, 0.4, 0.9, 0.5]
  let refreshed = false
  let microphone: Form['input_device'] = null
  let log: LogLine[] = []

  const writeLog = (source: LogLine['source'], text: string, level: LogLine['level'] = 'INFO') => {
    log.push({ time: clock.format(new Date()), source, level, text })
  }
  const t: ReturnType<typeof translatorFor> = (key, params) => translatorFor(language)(key, params)

  const bridge: Bridge = {
    async init() {
      writeLog('launcher', t('launcher.demo.ready'))
      return {
        platform: 'win32',
        language,
        view,
        tab,
        console: consoleFilters,
        form,
        options: demoOptions(language),
        state,
      }
    },
    async poll() {
      const now = Date.now()
      const meter = state.session === 'idle' ? 'on' : 'off'
      const levels = []
      const numLevels = Math.min(100, Math.floor((now - lastLevelAt) / LEVEL_INTERVAL))
      // Idle the launcher's meter listens; live the pipeline does (the wave shows it).
      if (meter === 'on' || state.session === 'live') {
        for (let sample = 0; sample < numLevels; sample++) {
          lastLevelAt += LEVEL_INTERVAL
          levels.push(microphone === 'built-in' ? 0.04 : (syllables[speechBlock] ?? 0.04))
          speechBlock = (speechBlock + 1) % 36
        }
      } else {
        lastLevelAt = now // nobody listens while starting or stopping: no backlog
      }
      const { microphone: mics, projector } = demoOptions(language)
      const result = {
        state,
        levels,
        meter,
        meter_error: null,
        log,
        options: refreshed ? { microphone: mics, projector } : null,
      } as const
      log = []
      refreshed = false
      return result
    },
    async check_speech(engine) {
      const wanted = engine === 'auto' ? 'kmynas' : engine
      return {
        wanted,
        runs: wanted,
        models: [
          ...(wanted === 'whisper' ? [] : [{ name: 'kmynas', state: 'ok' } as const]),
          ...(wanted === 'kmynas+canary' ? [{ name: 'canary', state: 'ok' } as const] : []),
        ],
      }
    },
    async run_self_test() {
      if (state.session !== 'idle') {
        return {
          ok: false,
          error: { code: 'session_busy', field: null, text: t('launcher.demo.sessionBusy') },
        }
      }
      if (state.self_test.status === 'running') {
        return {
          ok: false,
          error: { code: 'self_test_busy', field: null, text: t('launcher.demo.selfTestBusy') },
        }
      }
      state = { ...state, self_test: { status: 'running', text: null } }
      window.setTimeout(() => {
        const text = t('launcher.demo.selfTestOk')
        state = { ...state, self_test: { status: 'ok', text } }
        writeLog('launcher', text)
      }, 600)
      return { ok: true }
    },
    async check_port(port) {
      const value = Number(port)
      if (!Number.isInteger(value) || value < 1 || value > 65535) return { error: 'invalid_port' }
      return { free: true, program: null, pid: null, bibleLive: false }
    },
    async start(next) {
      if (state.self_test.status === 'running') {
        return {
          ok: false,
          error: { code: 'self_test_busy', field: null, text: t('launcher.demo.selfTestBusy') },
        }
      }
      const port = Number(next.port)
      if (!/^\d+$/.test(next.port.trim()) || port < 1 || port > 65_535) {
        return {
          ok: false,
          error: { code: 'invalid_port', field: 'port', text: t('launcher.demo.portError') },
        }
      }
      if (state.session !== 'idle') return { ok: true }
      form = { ...next }
      state = {
        ...idle,
        diagnostics: state.diagnostics,
        session: 'starting',
        server: 'starting',
        audio: 'starting',
      }
      writeLog('launcher', t('launcher.demo.starting', { port }))
      window.setTimeout(() => {
        if (state.session !== 'starting') return
        const host = `http://192.0.2.24:${port}`
        state = {
          ...state,
          session: 'live',
          server: 'ready',
          audio: 'running',
          started_at: Date.now(),
          speech: t('launcher.demo.speech', {
            engine:
              next.language === 'en' || next.speech_engine === 'whisper' ? 'Whisper' : 'Kmynas',
          }),
          speech_warning:
            next.device === 'cpu' && (next.language === 'en' || next.speech_engine === 'whisper'),
          network: next.allow_network
            ? {
                display_url: `${host}/display`,
                operator_url: `${host}/operator#token=demo-only`,
                token: 'demo-only',
                firewall: null,
                operator_qr: DEMO_QR,
                audience_qr: false,
                audience_qr_busy: false,
              }
            : null,
        }
        writeLog('server', t('launcher.demo.serverReady'))
        writeLog('audio', t('launcher.demo.listening'))
      }, 1_000)
      return { ok: true }
    },
    async stop() {
      if (state.session === 'idle' || state.session === 'stopping') return null
      state = { ...state, session: 'stopping', network: null }
      window.setTimeout(() => {
        state = { ...idle, diagnostics: state.diagnostics }
        lastLevelAt = Date.now() - LEVEL_INTERVAL
        speechBlock = 0
        writeLog('launcher', t('launcher.sessionStopped'))
      }, 250)
      return null
    },
    async refresh_devices() {
      refreshed = true
      writeLog('launcher', t('launcher.demo.refreshed'))
      return null
    },
    async set_microphone(device) {
      microphone = device
      form = { ...form, input_device: device }
      return null
    },
    async set_view(next) {
      view = next
      return null
    },
    async set_advanced_tab(next) {
      tab = next
      return null
    },
    async set_console_filters(source, level) {
      consoleFilters = { source, level }
      return null
    },
    async fit_window() {
      return null
    },
    async set_ui_language(code) {
      form = { ...form, ui_language: code }
      language =
        code === 'auto' ? (navigator.language.toLowerCase().startsWith('lt') ? 'lt' : 'en') : code
      return { language, options: demoOptions(language) }
    },
    async open_operator() {
      return null
    },
    async show_projector() {
      return null
    },
    async identify_screens() {
      return null
    },
    async open_logs() {
      return null
    },
    async collect_diagnostics() {
      if (state.diagnostics.status === 'running') return { ok: true }
      state = { ...state, diagnostics: { status: 'running', file: null, text: null } }
      window.setTimeout(() => {
        state = {
          ...state,
          diagnostics: {
            status: 'done',
            file: 'BibleLive-diagnostics-20261124-120000.zip',
            text: null,
          },
        }
      }, 500)
      return { ok: true }
    },
    async regenerate_token() {
      return { ok: state.session === 'idle' }
    },
    async allow_firewall() {
      return null
    },
    async set_audience_qr(visible) {
      if (state.network) state = { ...state, network: { ...state.network, audience_qr: visible } }
      return null
    },
    async copy_text() {
      throw new Error('Clipboard unavailable in the preview')
    },
  }
  previews.add(bridge)
  return bridge
}
