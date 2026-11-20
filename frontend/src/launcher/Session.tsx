import { useLayoutEffect, useRef } from 'react'
import { useI18n } from '../shared/i18nContext'
import type { Form, StartError } from './bridge'
import { Choice } from './Choice'
import { formatElapsed } from './elapsed'
import { Icon } from './Icon'
import type { LauncherData } from './launcherReducer'
import { noSignal } from './noSignal'

const liveWave = [
  7, 13, 9, 19, 27, 16, 31, 52, 37, 71, 34, 49, 21, 12, 22, 44, 78, 61, 95, 69, 47, 26, 38, 51, 24,
  17, 31, 56, 38, 73, 53, 28, 18, 28, 51, 36, 21, 28, 13, 18, 7, 10,
]
const WAVE_LENGTH = liveWave.length

function Waveform({ data }: { data: LauncherData }) {
  const live = data.state.session === 'live'
  const recent = data.meter === 'on' ? data.levels.slice(-WAVE_LENGTH) : []
  return (
    <div className="bl-signal" aria-hidden="true">
      <div className="bl-wave">
        {liveWave.map((height, index) => {
          const level = recent[index - (WAVE_LENGTH - recent.length)]?.level ?? 0
          return (
            <span
              key={index}
              style={{
                height: live ? `${height * 0.64}%` : `${Math.max(3, level * 82)}%`,
                opacity: 0.3 + Math.sin((Math.PI * index) / WAVE_LENGTH) * 0.68,
              }}
            />
          )
        })}
      </div>
    </div>
  )
}

interface HeroProps {
  data: LauncherData
  pending: 'start' | 'stop' | null
  stopped: boolean
  error?: string
  onSession: () => void
  onOperator: () => void
  onDetails: () => void
}

export function Hero({
  data,
  pending,
  stopped,
  error,
  onSession,
  onOperator,
  onDetails,
}: HeroProps) {
  const { t } = useI18n()
  const { state, now, audioWasRunning } = data
  const live = state.session === 'live'
  const starting = state.session === 'starting' || pending === 'start'
  const stopping = state.session === 'stopping' || pending === 'stop'
  const canOpen = live && !stopping
  const status =
    starting || live
      ? state.started_at !== null
        ? t('launcher.liveElapsed', { elapsed: formatElapsed(state.started_at, now) })
        : t(live ? 'launcher.listening' : 'launcher.startingServices')
      : t(stopping ? 'launcher.stopping' : stopped ? 'launcher.sessionStopped' : 'launcher.ready')
  const warning =
    state.error?.text ||
    (state.server === 'error'
      ? t('launcher.serverError')
      : state.audio === 'error'
        ? t('launcher.audioError')
        : state.audio === 'starting' && audioWasRunning
          ? t('launcher.audioRestarting')
          : null)

  return (
    <section className="bl-hero" aria-labelledby="bl-session">
      <div className="bl-hero-copy">
        <div className="bl-status-strip">
          <span className="bl-status">
            <span className="bl-dot" aria-hidden="true" />
            {status}
          </span>
          {warning && (
            <span className="bl-warning">
              <span role="status">{warning}</span>
              <button type="button" onClick={onDetails}>
                {t('launcher.showDetails')}
              </button>
            </span>
          )}
        </div>
        <h1 id="bl-session">
          {t(
            stopping
              ? 'launcher.stopping'
              : live
                ? 'launcher.sessionRunning'
                : starting
                  ? 'launcher.sessionStarting'
                  : 'launcher.sessionTitle'
          )}
        </h1>
        <p className="bl-hero-help">{t(live ? 'launcher.liveHelp' : 'launcher.sessionHelp')}</p>
        <div className="bl-actions">
          <button
            type="button"
            className="bl-button bl-primary"
            onClick={onSession}
            disabled={stopping || (pending === 'start' && state.session === 'idle')}
          >
            <span className="bl-play" aria-hidden="true" />
            {t(
              stopping
                ? 'launcher.stopping'
                : live || state.session === 'starting'
                  ? 'launcher.stop'
                  : starting
                    ? 'launcher.starting'
                    : 'launcher.start'
            )}
          </button>
          <button
            type="button"
            className="bl-button bl-hero-secondary"
            disabled={!canOpen}
            onClick={onOperator}
            title={!canOpen ? t('launcher.operatorUnavailable') : undefined}
          >
            <Icon name="external" /> {t('launcher.openOperator')}
          </button>
        </div>
        {error && (
          <p className="bl-hero-error" role="alert">
            {error}
          </p>
        )}
      </div>
      <Waveform data={data} />
    </section>
  )
}

function MicMeter({ level, inUse }: { level: number; inUse: boolean }) {
  const { t } = useI18n()
  // Compare against the previous rendered target to choose the release speed.
  const previous = useRef(level)
  const rising = level >= previous.current
  useLayoutEffect(() => {
    previous.current = level
  }, [level])
  const label = t(inUse ? 'launcher.micInUse' : 'launcher.micLevel')
  return (
    <div className="bl-mic-meter" data-in-use={inUse}>
      <span>{label}</span>
      <div
        className="bl-meter-track"
        role="meter"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={1}
        aria-valuenow={level}
      >
        <span
          style={{ transform: `scaleX(${level})`, transitionDuration: rising ? '35ms' : '300ms' }}
        />
      </div>
    </div>
  )
}

interface SetupProps {
  data: LauncherData
  form: Form
  locked: boolean
  canProject: boolean
  error: StartError | null
  onChange: <K extends keyof Form>(key: K, value: Form[K]) => void
  onMicrophone: (device: Form['input_device']) => void
  onRefresh: () => void
  onProjector: () => void
}

export function Setup({
  data,
  form,
  locked,
  canProject,
  error,
  onChange,
  onMicrophone,
  onRefresh,
  onProjector,
}: SetupProps) {
  const { t } = useI18n()
  const { levels, meter, meterError, now, options } = data
  const level = meter === 'on' && !locked ? (levels[levels.length - 1]?.level ?? 0) : 0
  const problem = error?.field === 'input_device' ? error.text : null
  return (
    <section className="bl-setup" aria-labelledby="bl-setup-heading">
      <div className="bl-section-head">
        <h2 id="bl-setup-heading">{t('launcher.setup')}</h2>
        <button type="button" className="bl-button bl-quiet" disabled={locked} onClick={onRefresh}>
          <Icon name="refresh" /> {t('launcher.refreshDevices')}
        </button>
      </div>
      <div className="bl-sources">
        <Choice
          label={
            <>
              <Icon name="language" /> {t('launcher.spokenLanguage')}
            </>
          }
          value={form.language}
          options={options.language}
          disabled={locked}
          onChange={value => value !== null && onChange('language', value)}
        />
        <div className="bl-mic-field">
          <Choice
            label={
              <>
                <Icon name="mic" /> {t('launcher.microphone')}
              </>
            }
            value={form.input_device}
            options={options.microphone}
            disabled={locked}
            onChange={onMicrophone}
            errorId={problem ? 'bl-input-error' : undefined}
          />
          <MicMeter level={level} inUse={locked} />
          {problem && (
            <p id="bl-input-error" className="bl-error" role="alert">
              {problem}
            </p>
          )}
          {meter === 'error' && meterError && (
            <p className="bl-error" role="status">
              {meterError}
            </p>
          )}
          {!locked && noSignal(meter, levels, now) && (
            <p className="bl-muted" role="status">
              {t('launcher.noSignal')}
            </p>
          )}
        </div>
        <Choice
          label={
            <>
              <Icon name="monitor" /> {t('launcher.projector')}
            </>
          }
          value={form.projector_monitor}
          options={options.projector}
          disabled={locked}
          onChange={value => onChange('projector_monitor', value)}
        />
      </div>
      <div className="bl-setup-foot">
        <span className="bl-muted">{t('launcher.deviceHint')}</span>
        <button
          type="button"
          className="bl-button bl-quiet"
          disabled={!canProject}
          title={!canProject ? t('launcher.projectorUnavailable') : undefined}
          onClick={onProjector}
        >
          <Icon name="monitor" /> {t('launcher.showProjector')}
        </button>
      </div>
    </section>
  )
}
