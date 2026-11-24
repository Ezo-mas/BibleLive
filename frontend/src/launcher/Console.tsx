import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import type { RefObject } from 'react'
import { useI18n } from '../shared/i18nContext'
import type { Bridge, LogLine } from './bridge'
import { filterLog, logLevels, logSources, logText } from './consoleFilter'
import type { LogSourceFilter } from './consoleFilter'
import { Icon } from './Icon'
import type { LauncherData } from './launcherReducer'

interface Props {
  data: LauncherData
  logRef: RefObject<HTMLDivElement>
  visible: boolean
  initialFilters: { source: LogSourceFilter; level: LogLine['level'] }
  onFilters: (source: LogSourceFilter, level: LogLine['level']) => void
  bridge: Bridge
  onLogs: () => void
  onDiagnostics: () => void
}

export function Console({
  data,
  logRef,
  visible,
  initialFilters,
  onFilters,
  bridge,
  onLogs,
  onDiagnostics,
}: Props) {
  const { t } = useI18n()
  const [source, setSource] = useState<LogSourceFilter>(initialFilters.source)
  const [minimum, setMinimum] = useState<LogLine['level']>(initialFilters.level)
  const follow = useRef(true)
  // The log counter when the operator scrolled up, or null while following the latest line.
  const [pausedAt, setPausedAt] = useState<number | null>(null)
  const [copy, setCopy] = useState<'idle' | 'done' | 'failed'>('idle')
  const copyTimer = useRef<number>()
  const { log, logCount, state } = data
  const { diagnostics } = state
  const collecting = diagnostics.status === 'running'
  const hint = t('launcher.diagnosticsHint')
  const detail = collecting
    ? t('launcher.diagnosticsCollecting')
    : (diagnostics.file ?? diagnostics.text)
  const entries = useMemo(() => {
    const visibleLines = new Set(filterLog(log, source, minimum))
    return log.flatMap((line, index) =>
      visibleLines.has(line) ? [{ line, id: logCount - log.length + index }] : []
    )
  }, [log, logCount, source, minimum])
  const rows = useMemo(
    () =>
      entries.map(({ line, id }) => (
        <li key={id} data-level={line.level}>
          <time>{line.time}</time>
          <b>{t(`launcher.level.${line.level}`)}</b>
          <span className="bl-tag">{t(`launcher.source.${line.source}`)}</span>
          <span>{line.text}</span>
        </li>
      )),
    [entries, t]
  )

  useEffect(() => () => window.clearTimeout(copyTimer.current), [])

  // Copies what the console shows now: the active source and level filters apply.
  async function copyLogs() {
    const text = logText(
      entries.map(({ line }) => line),
      {
        level: level => t(`launcher.level.${level}`),
        source: source => t(`launcher.source.${source}`),
      }
    )
    let result: 'done' | 'failed' = 'done'
    try {
      try {
        await navigator.clipboard.writeText(text)
      } catch {
        await bridge.copy_text(text)
      }
    } catch {
      result = 'failed'
    }
    setCopy(result)
    window.clearTimeout(copyTimer.current)
    copyTimer.current = window.setTimeout(() => setCopy('idle'), 2000)
  }

  function jumpToLatest() {
    const log = logRef.current
    if (log) log.scrollTop = log.scrollHeight
    follow.current = true
    setPausedAt(null)
    log?.focus()
  }

  const newLines = pausedAt === null ? 0 : entries.filter(({ id }) => id >= pausedAt).length

  useLayoutEffect(() => {
    if (visible && follow.current && logRef.current)
      logRef.current.scrollTop = logRef.current.scrollHeight
  }, [visible, entries, logRef])

  return (
    <section className="bl-console" aria-labelledby="bl-console-heading">
      <div className="bl-console-head">
        <h2 id="bl-console-heading">
          <Icon name="terminal" /> {t('launcher.console')}
        </h2>
        <div className="bl-actions">
          <button
            type="button"
            className="bl-button bl-quiet"
            onClick={onLogs}
            aria-label={t('launcher.logFiles')}
            title={t('launcher.logFiles')}
          >
            <Icon name="folder" />{' '}
            <span className="bl-log-files-label">{t('launcher.logFiles')}</span>
          </button>
          <button
            type="button"
            className="bl-button bl-quiet bl-copy-logs"
            data-state={copy}
            onClick={() => void copyLogs()}
            aria-label={t('launcher.copyLogs')}
            title={t('launcher.copyLogs')}
          >
            <Icon name={copy === 'done' ? 'check' : copy === 'failed' ? 'warning' : 'copy'} />{' '}
            <span className="bl-log-files-label">
              {t(
                copy === 'done'
                  ? 'launcher.copyLogsDone'
                  : copy === 'failed'
                    ? 'launcher.copyLogsFailed'
                    : 'launcher.copyLogs'
              )}
            </span>
          </button>
          <button
            type="button"
            className="bl-button bl-quiet bl-diagnostics"
            onClick={onDiagnostics}
            disabled={collecting}
            aria-busy={collecting}
            title={detail ? `${hint}\n${detail}` : hint}
          >
            <Icon name={collecting ? 'refresh' : 'archive'} /> {t('launcher.diagnostics')}
          </button>
          {/* Announces progress and the result; the visible place for them is the console log. */}
          <span className="bl-sr-only" role="status">
            {detail}
          </span>
          <span className="bl-sr-only" role="status">
            {copy === 'done' && t('launcher.copied')}
            {copy === 'failed' && t('launcher.copyFailed')}
          </span>
        </div>
      </div>
      <div className="bl-services">
        <span>
          <i className="bl-dot" data-service={state.server} aria-hidden="true" />
          {t('launcher.source.server')} <strong>{t(`launcher.server.${state.server}`)}</strong>
        </span>
        <span>
          <i className="bl-dot" data-service={state.audio} aria-hidden="true" />
          {t('launcher.source.audio')} <strong>{t(`launcher.audio.${state.audio}`)}</strong>
        </span>
      </div>
      <div className="bl-filters">
        <div className="bl-chips" role="group" aria-label={t('launcher.logSources')}>
          {logSources.map(name => (
            <button
              type="button"
              key={name}
              aria-pressed={name === source}
              onClick={() => {
                setSource(name)
                onFilters(name, minimum)
              }}
            >
              {t(`launcher.source.${name}`)}
            </button>
          ))}
        </div>
        <label>
          <span>{t('launcher.minimumLevel')}</span>
          <select
            value={minimum}
            onChange={event => {
              const level = event.currentTarget.value as LogLine['level']
              setMinimum(level)
              onFilters(source, level)
            }}
          >
            {logLevels.map(level => (
              <option key={level} value={level}>
                {t(`launcher.level.${level}`)}
              </option>
            ))}
          </select>
        </label>
      </div>
      <div
        className="bl-log"
        role="region"
        aria-label={t('launcher.sessionLog')}
        tabIndex={0}
        ref={logRef}
        onScroll={event => {
          const { scrollHeight, clientHeight, scrollTop } = event.currentTarget
          const atBottom = scrollHeight - clientHeight - scrollTop <= 4
          follow.current = atBottom
          setPausedAt(paused => (atBottom ? null : (paused ?? logCount)))
        }}
      >
        {rows.length ? <ol>{rows}</ol> : <p>{t('launcher.noLogLines')}</p>}
      </div>
      {pausedAt !== null && (
        <button type="button" className="bl-log-paused" onClick={jumpToLatest}>
          <Icon name="arrowDown" />{' '}
          {newLines ? t('launcher.logPausedNew', { count: newLines }) : t('launcher.logPaused')}
        </button>
      )}
      <div className="bl-console-foot" data-warning={state.speech_warning}>
        {state.speech && (
          <span>
            <Icon name="speech" /> {state.speech}
          </span>
        )}
      </div>
    </section>
  )
}
