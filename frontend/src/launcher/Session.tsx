import type { CSSProperties, RefObject } from 'react'
import { useI18n } from '../shared/i18nContext'
import type { Form, StartError } from './bridge'
import { Choice } from './Choice'
import { formatElapsed } from './elapsed'
import { Icon } from './Icon'
import type { LauncherData } from './launcherReducer'
import {
  dbfsToLevel,
  formatPeak,
  MAX_DBFS,
  METER_TICKS,
  MIN_DBFS,
  RED_DBFS,
  YELLOW_DBFS,
} from './levelEnvelope'
import { noSignal } from './noSignal'

const liveWave = [
  7, 13, 9, 19, 27, 16, 31, 52, 37, 71, 34, 49, 21, 12, 22, 44, 78, 61, 95, 69, 47, 26, 38, 51, 24,
  17, 31, 56, 38, 73, 53, 28, 18, 28, 51, 36, 21, 28, 13, 18, 7, 10,
]
const WAVE_LENGTH = liveWave.length

function Waveform({ waveRef }: { waveRef: RefObject<HTMLDivElement> }) {
  return (
    <div className="bl-signal" aria-hidden="true">
      <div className="bl-wave" ref={waveRef}>
        {liveWave.map((height, index) => {
          const profile = (height + liveWave[WAVE_LENGTH - 1 - index]) / 2
          return (
            <span
              key={index}
              style={
                {
                  '--wave-profile': (profile * 0.85) / 100,
                  '--wave-live': (height * 0.64) / 100,
                  opacity: 0.3 + Math.sin((Math.PI * index) / (WAVE_LENGTH - 1)) * 0.68,
                } as CSSProperties
              }
            />
          )
        })}
      </div>
    </div>
  )
}

interface HeroProps {
  waveRef: RefObject<HTMLDivElement>
  data: LauncherData
  pending: 'start' | 'stop' | null
  stopped: boolean
  error?: string
  onSession: () => void
  onOperator: () => void
  onDetails: () => void
}

export function Hero({
  waveRef,
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
      <Waveform waveRef={waveRef} />
    </section>
  )
}

function MicMeter({
  meterRef,
  inUse,
  quiet,
  meterError,
}: {
  meterRef: RefObject<HTMLDivElement>
  inUse: boolean
  quiet: boolean
  meterError: string | null
}) {
  const { t } = useI18n()
  const label = t(inUse ? 'launcher.micInUse' : 'launcher.micLevel')
  const warning = quiet ? t('launcher.noSignal') : meterError
  const details = quiet ? t('launcher.noSignalDetails') : undefined
  return (
    <div className="bl-mic-meter" data-in-use={inUse} ref={meterRef}>
      <div className="bl-meter-label">
        <span className="bl-meter-caption" role="status" data-warning={Boolean(warning)}>
          {warning && <Icon name="warning" />}
          <span aria-hidden={!warning} title={details ?? meterError ?? undefined}>
            {warning || label}
          </span>
        </span>
        <span className="bl-meter-readout" aria-hidden="true" hidden={inUse}>
          {formatPeak(0)}
        </span>
      </div>
      {details && (
        <span id="bl-meter-details" hidden>
          {details}
        </span>
      )}
      <div
        className="bl-meter-track"
        style={
          {
            '--meter-yellow-start': `${dbfsToLevel(YELLOW_DBFS) * 100}%`,
            '--meter-red-start': `${dbfsToLevel(RED_DBFS) * 100}%`,
          } as CSSProperties
        }
        role="meter"
        aria-label={label}
        aria-describedby={details ? 'bl-meter-details' : undefined}
        aria-valuemin={MIN_DBFS}
        aria-valuemax={MAX_DBFS}
        aria-valuenow={MIN_DBFS}
        aria-valuetext={t('launcher.meterSilence')}
      >
        <span className="bl-meter-fill" />
        <span className="bl-meter-peak" />
      </div>
      <div className="bl-meter-scale" aria-hidden="true">
        {METER_TICKS.map(({ db, position }) => (
          <span key={db} style={{ left: `${position}%` }}>
            {db}
          </span>
        ))}
      </div>
    </div>
  )
}

interface SetupProps {
  meterRef: RefObject<HTMLDivElement>
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
  meterRef,
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
          <MicMeter
            meterRef={meterRef}
            inUse={locked}
            quiet={!locked && noSignal(meter, levels, now)}
            meterError={meter === 'error' ? meterError : null}
          />
          {problem && (
            <p id="bl-input-error" className="bl-error" role="alert">
              {problem}
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
