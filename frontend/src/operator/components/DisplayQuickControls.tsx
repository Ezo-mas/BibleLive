import { useRef } from 'react'
import { Button, XIcon, SegmentedOption } from '../../components'
import { useI18n } from '../../shared/i18nContext'
import { VERSE_LIMIT_OPTIONS } from '../../shared/verseText'
import { autoModeOption } from '../autoMode'
import type { VerseLimit } from '../../shared/verseText'
import type { AutoMode } from '../../shared/types'
import type { FlashTarget } from '../hooks/useOperatorState'

export interface DisplayQuickControlsProps {
  canClearDisplay: boolean
  flash: FlashTarget | null
  onClearDisplay: () => void
  verseLimit: VerseLimit
  autoMode: AutoMode
  /** The display settings are server-owned, so they are read-only while disconnected. */
  disabled: boolean
  onVerseLimitChange: (next: VerseLimit) => void
  onAutoModeChange: (next: AutoMode) => void
}

/** The mode On restores when none has been chosen yet: the most conservative one. */
const DEFAULT_ON_MODE: AutoMode = 'high'

/**
 * The few display controls an operator reaches for during a service, in one row
 * under the preview: Clear display, how many verses to show, and auto-approve.
 *
 * Nothing here is new: Clear display, the verse limit and the auto-approve mode
 * are the same actions the settings dialog sends. Auto-approve is an Off / On
 * segmented control like every other setting, a shortcut for the mode — On
 * restores the last mode that was on (High until one has been chosen, held in
 * memory only), Off sends `off`. The full list of modes, the display background
 * and everything else stay in Settings.
 */
export function DisplayQuickControls({
  canClearDisplay,
  flash,
  onClearDisplay,
  verseLimit,
  autoMode,
  disabled,
  onVerseLimitChange,
  onAutoModeChange,
}: DisplayQuickControlsProps) {
  const { t } = useI18n()
  const lastOnMode = useRef<AutoMode>(DEFAULT_ON_MODE)
  if (autoMode !== 'off') lastOnMode.current = autoMode
  const on = autoMode !== 'off'
  const options: VerseLimit[] = [...VERSE_LIMIT_OPTIONS, 'all']

  return (
    <div className="operator-quick">
      <div className="operator-quick-group">
        <span className="operator-quick-label">{t('quick.screenLabel')}</span>
        <Button
          variant="secondary"
          icon={<XIcon />}
          onClick={onClearDisplay}
          disabled={!canClearDisplay}
          className={flash === 'clear' ? 'btn-flash' : ''}
          title={t('quick.clearTitle')}
          aria-keyshortcuts="B"
        >
          {t('stage.clearDisplay')}
        </Button>
      </div>

      <div className="operator-quick-group">
        <span className="operator-quick-label">{t('quick.versesLabel')}</span>
        <div className="segmented" role="group" aria-label={t('quick.versesAria')}>
          {options.map(option => (
            <SegmentedOption
              key={option}
              aria-pressed={verseLimit === option}
              disabled={disabled}
              onClick={() => onVerseLimitChange(option)}
            >
              {option === 'all' ? t('settings.versesAll') : option}
            </SegmentedOption>
          ))}
        </div>
      </div>

      <div className="operator-quick-group operator-quick-auto">
        {/* A short caption: the full name is the group's accessible name. */}
        <span className="operator-quick-label">{t('quick.autoCaption')}</span>
        <div className="segmented" role="group" aria-label={t('quick.autoLabel')}>
          <SegmentedOption
            aria-pressed={!on}
            disabled={disabled}
            onClick={() => on && onAutoModeChange('off')}
          >
            {t('quick.autoOff')}
          </SegmentedOption>
          <SegmentedOption
            aria-pressed={on}
            // Which mode On stands for: the one that is on now, or the one it will restore.
            title={t(autoModeOption(on ? autoMode : lastOnMode.current).labelKey)}
            disabled={disabled}
            onClick={() => !on && onAutoModeChange(lastOnMode.current)}
          >
            {t('quick.autoOn')}
          </SegmentedOption>
        </div>
      </div>
    </div>
  )
}
