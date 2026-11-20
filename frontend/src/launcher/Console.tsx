import { useLayoutEffect, useMemo, useRef, useState } from 'react'
import type { RefObject } from 'react'
import { useI18n } from '../shared/i18nContext'
import type { LogLine } from './bridge'
import { filterLog, logLevels, logSources } from './consoleFilter'
import type { LogSourceFilter } from './consoleFilter'
import { Icon } from './Icon'
import type { LauncherData } from './launcherReducer'

interface Props {
  data: LauncherData
  logRef: RefObject<HTMLDivElement>
  visible: boolean
  onLogs: () => void
}

export function Console({ data, logRef, visible, onLogs }: Props) {
  const { t } = useI18n()
  const [source, setSource] = useState<LogSourceFilter>('all')
  const [minimum, setMinimum] = useState<LogLine['level']>('DEBUG')
  const follow = useRef(true)
  const { log, logCount, state } = data
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
        <button type="button" className="bl-button bl-quiet" onClick={onLogs}>
          <Icon name="folder" /> {t('launcher.logFiles')}
        </button>
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
              onClick={() => setSource(name)}
            >
              {t(`launcher.source.${name}`)}
            </button>
          ))}
        </div>
        <label>
          <span>{t('launcher.minimumLevel')}</span>
          <select
            value={minimum}
            onChange={event => setMinimum(event.currentTarget.value as LogLine['level'])}
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
          follow.current = scrollHeight - clientHeight - scrollTop <= 4
        }}
      >
        {rows.length ? <ol>{rows}</ol> : <p>{t('launcher.noLogLines')}</p>}
      </div>
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
