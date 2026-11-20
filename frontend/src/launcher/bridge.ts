import type { Language } from '../shared/i18n'
import { createDemoBridge } from './demoBridge'

export interface Option {
  value: string | null
  label: string
}

export type View = 'simple' | 'advanced'
export type UILanguage = 'auto' | Language
export type Platform = 'win32' | 'darwin' | 'linux'
export type Meter = 'off' | 'starting' | 'on' | 'error'

export interface Form {
  language: string
  input_device: string | null
  projector_monitor: string | null
  auto_mode: string
  ui_language: string
  model: string
  device: string
  compute_type: string
  port: string
  log_level: string
  extra_args: string
  open_display: boolean
  open_operator: boolean
  speech_engine: string
  allow_network: boolean
  session_audio: boolean
}

export type Options = Record<
  | 'language'
  | 'microphone'
  | 'projector'
  | 'speech_engine'
  | 'model'
  | 'device'
  | 'compute_type'
  | 'auto_mode'
  | 'ui_language'
  | 'log_level',
  Option[]
>

export interface Network {
  display_url: string
  operator_url: string
  token: string
  firewall: { message: string; button_enabled: boolean } | null
}

export interface State {
  session: 'idle' | 'starting' | 'live' | 'stopping'
  server: 'stopped' | 'starting' | 'ready' | 'error'
  audio: 'stopped' | 'starting' | 'running' | 'error'
  speech: string | null
  speech_warning: boolean
  started_at: number | null
  network: Network | null
  error: { text: string } | null
}

export interface LogLine {
  time: string
  source: 'launcher' | 'server' | 'audio'
  level: 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR'
  text: string
}

export interface InitResult {
  platform: Platform
  language: Language
  view: View
  form: Form
  options: Options
  state: State
}

export interface PollResult {
  state: State
  levels: number[]
  meter: Meter
  meter_error: string | null
  log: LogLine[]
  options: Pick<Options, 'microphone' | 'projector'> | null
}

export interface StartError {
  code: string
  text: string
  field: 'port' | 'extra_args' | 'input_device' | null
}

export interface Bridge {
  init(): Promise<InitResult>
  poll(): Promise<PollResult>
  start(form: Form): Promise<{ ok: true } | { ok: false; error: StartError }>
  stop(): Promise<null>
  refresh_devices(): Promise<null>
  set_microphone(device: string | null): Promise<null>
  open_operator(): Promise<null>
  show_projector(): Promise<null>
  open_logs(): Promise<null>
  allow_firewall(): Promise<null>
  set_ui_language(code: UILanguage): Promise<{ language: Language; options: Options }>
  set_view(view: View): Promise<null>
  fit_window(contentHeight: number, viewportHeight: number): Promise<null>
  copy_text(text: string): Promise<null>
}

declare global {
  interface Window {
    pywebview?: { api?: Bridge }
    chrome?: { webview?: unknown }
    webkit?: { messageHandlers?: { jsBridge?: unknown } }
  }
}

let connection: Promise<Bridge> | undefined

export function getBridge(): Promise<Bridge> {
  return (connection ??= new Promise(resolve => {
    // The demo is for the vite dev server only; a packaged launcher never runs it.
    if (import.meta.env.DEV && new URLSearchParams(window.location.search).has('demo')) {
      resolve(createDemoBridge())
      return
    }
    function onReady() {
      const api = window.pywebview?.api
      if (typeof api?.init === 'function' && typeof api.poll === 'function') {
        window.removeEventListener('pywebviewready', onReady)
        resolve(api)
      }
    }
    // Native hosts expose their message transport before pywebview injects its API.
    if (
      import.meta.env.DEV &&
      !window.pywebview &&
      !window.chrome?.webview &&
      !window.webkit?.messageHandlers?.jsBridge
    ) {
      resolve(createDemoBridge())
      return
    }
    window.addEventListener('pywebviewready', onReady)
    onReady()
  }))
}
