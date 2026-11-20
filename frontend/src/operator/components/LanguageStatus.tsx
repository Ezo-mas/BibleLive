import { WaveformIcon } from '../../components'
import { useI18n } from '../../shared/i18nContext'
import { spokenLanguageLabel } from '../languageApi'

interface LanguageStatusProps {
  /** The language the pipeline confirmed, or null while it is unknown. */
  language: string | null
  /** The pipeline is offline, so this is its last reported language. */
  stale?: boolean
}

export function LanguageStatus({ language, stale = false }: LanguageStatusProps) {
  const { t } = useI18n()
  if (!language) return null

  const label = t(stale ? 'speech.staleStatus' : 'speech.status', {
    language: spokenLanguageLabel(language, t),
  })

  return (
    <span
      className={`operator-language-status${stale ? ' operator-language-status--stale' : ''}`}
      title={label}
    >
      <WaveformIcon aria-hidden="true" />
      <span aria-hidden="true">{language.toUpperCase()}</span>
      <span className="sr-only">{label}</span>
    </span>
  )
}
