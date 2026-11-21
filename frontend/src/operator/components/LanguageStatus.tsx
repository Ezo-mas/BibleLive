import { useSyncExternalStore } from 'react'
import { WaveformIcon } from '../../components'
import { useI18n } from '../../shared/i18nContext'
import { formatPeak, levelToDbfs, meterZone, SIGNAL_THRESHOLD } from '../../shared/levels'
import { spokenLanguageLabel } from '../languageApi'
import type { InputLevelsStore } from '../state/inputLevelsStore'

interface LanguageStatusProps {
  /** The language the pipeline confirmed, or null while it is unknown. */
  language: string | null
  /** The pipeline is offline, so this is its last reported language. */
  stale?: boolean
  silent?: boolean
  inputLevels: InputLevelsStore
}

export function LanguageStatus({
  language,
  stale = false,
  silent = false,
  inputLevels,
}: LanguageStatusProps) {
  const { t } = useI18n()
  const snapshot = useSyncExternalStore(inputLevels.subscribe, inputLevels.getSnapshot)
  if (!language) return null

  const label = t(stale ? 'speech.staleStatus' : 'speech.status', {
    language: spokenLanguageLabel(language, t),
  })
  const meter = stale ? null : snapshot
  const tooltip = meter?.tooltip
  const title = tooltip
    ? `${label} · ${t('speech.inputLevels', {
        input: formatPeak(tooltip.input),
        peak: formatPeak(tooltip.peak),
      })}`
    : label
  const modifier = stale ? 'stale' : silent ? 'silent' : null

  return (
    <span
      className={`operator-language-status${modifier ? ` operator-language-status--${modifier}` : ''}`}
      title={title}
    >
      {meter ? (
        <span className="operator-input-meter" aria-hidden="true">
          {meter.bars.map((level, index) => (
            <span
              key={index}
              className="operator-input-meter-bar"
              data-zone={level < SIGNAL_THRESHOLD ? 'quiet' : meterZone(levelToDbfs(level))}
              style={{ transform: `scaleY(${Math.max(0.18, level)})` }}
            />
          ))}
        </span>
      ) : (
        <WaveformIcon aria-hidden="true" />
      )}
      <span aria-hidden="true">{language.toUpperCase()}</span>
      <span className="sr-only">{label}</span>
    </span>
  )
}
