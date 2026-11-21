import { useEffect, useRef, useState } from 'react'
import { Button, SegmentedOption } from '../../components'
import { useI18n } from '../../shared/i18nContext'
import type { MessageKey } from '../../shared/i18n'
import {
  DEFAULT_STREAM_OVERLAY_OPTIONS,
  parseStreamOverlayOptions,
  streamOverlayQuery,
} from '../../display/streamOverlayOptions'
import type { StreamOverlayOptions } from '../../display/streamOverlayOptions'

/** Where this browser remembers the options last picked, as the overlay's own query. */
export const STREAM_OVERLAY_STORAGE_KEY = 'biblelive.streamOverlayQuery'

/** The OBS Browser source is sized to the stream; the preview draws that frame scaled down. */
const FRAME_WIDTH = 1920
const FRAME_HEIGHT = 1080

const HEIGHT_OPTIONS = [20, 30, 40, 50] as const

function readStoredOptions(): StreamOverlayOptions {
  try {
    const stored = window.localStorage.getItem(STREAM_OVERLAY_STORAGE_KEY)
    return stored ? parseStreamOverlayOptions(stored) : DEFAULT_STREAM_OVERLAY_OPTIONS
  } catch {
    return DEFAULT_STREAM_OVERLAY_OPTIONS
  }
}

interface Choice<K extends keyof StreamOverlayOptions> {
  key: K
  label: MessageKey
  hint: MessageKey
  options: readonly { value: StreamOverlayOptions[K]; label: MessageKey | string }[]
}

const CHOICES = [
  {
    key: 'position',
    label: 'obs.position',
    hint: 'obs.positionHint',
    options: [
      { value: 'bottom', label: 'obs.bottom' },
      { value: 'top', label: 'obs.top' },
    ],
  } satisfies Choice<'position'>,
  {
    key: 'heightPct',
    label: 'obs.height',
    hint: 'obs.heightHint',
    options: HEIGHT_OPTIONS.map(value => ({ value, label: `${value}%` })),
  } satisfies Choice<'heightPct'>,
  {
    key: 'style',
    label: 'obs.background',
    hint: 'obs.backgroundHint',
    options: [
      { value: 'band', label: 'obs.band' },
      { value: 'none', label: 'obs.textOnly' },
    ],
  } satisfies Choice<'style'>,
  {
    key: 'font',
    label: 'obs.font',
    hint: 'obs.fontHint',
    options: [
      { value: 'serif', label: 'obs.serif' },
      { value: 'sans', label: 'obs.sans' },
    ],
  } satisfies Choice<'font'>,
  {
    key: 'align',
    label: 'obs.align',
    hint: 'obs.alignHint',
    options: [
      { value: 'left', label: 'obs.left' },
      { value: 'center', label: 'obs.center' },
    ],
  } satisfies Choice<'align'>,
] as const

/**
 * The stream overlay (`/display?mode=obs`) for OBS: its layout, a live preview and the
 * link to paste into an OBS Browser source.
 *
 * The overlay takes its layout from its URL, so these choices build that link and are
 * this console's own (remembered in this browser), not server settings: changing one
 * means pasting the new link into OBS. The preview is the real overlay page in its
 * socket-free preview mode, cycling sample passages with the real fades, so it never
 * counts as an audience display.
 */
export function StreamOverlaySettings() {
  const { t } = useI18n()
  const [options, setOptions] = useState<StreamOverlayOptions>(readStoredOptions)
  const [copied, setCopied] = useState(false)
  const [scale, setScale] = useState(0)
  const frameRef = useRef<HTMLDivElement>(null)
  const linkRef = useRef<HTMLInputElement>(null)
  const copiedTimerRef = useRef<number | undefined>(undefined)

  const query = streamOverlayQuery(options)
  const link = `${window.location.origin}/display${query}`

  useEffect(() => {
    try {
      window.localStorage.setItem(STREAM_OVERLAY_STORAGE_KEY, query)
    } catch {
      // Private window or blocked storage: the choices just are not remembered.
    }
  }, [query])

  useEffect(() => () => window.clearTimeout(copiedTimerRef.current), [])

  // Draw the 1920x1080 overlay page scaled to the preview's width, so its text sizes
  // and the readable minimum come out exactly as they will on the stream.
  useEffect(() => {
    const frame = frameRef.current
    if (!frame) return
    const fit = () => setScale(frame.clientWidth / FRAME_WIDTH)
    fit()
    if (typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(fit)
    observer.observe(frame)
    return () => observer.disconnect()
  }, [])

  const choose = <K extends keyof StreamOverlayOptions>(key: K, value: StreamOverlayOptions[K]) => {
    setOptions(current => ({ ...current, [key]: value }))
    setCopied(false)
  }

  const copyLink = async () => {
    try {
      // The clipboard API needs a secure page (localhost); a console opened over the
      // network falls back to copying the selected text.
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(link)
      } else {
        linkRef.current?.select()
        document.execCommand('copy')
      }
      setCopied(true)
      window.clearTimeout(copiedTimerRef.current)
      copiedTimerRef.current = window.setTimeout(() => setCopied(false), 2000)
    } catch {
      linkRef.current?.select()
    }
  }

  return (
    <>
      {CHOICES.map(choice => (
        <div className="operator-setting" key={choice.key}>
          <div className="operator-setting-text">
            <span className="operator-setting-label">{t(choice.label)}</span>
            <p className="operator-setting-hint">{t(choice.hint)}</p>
          </div>
          <div className="segmented" role="group" aria-label={t(choice.label)}>
            {choice.options.map(option => (
              <SegmentedOption
                key={String(option.value)}
                aria-pressed={options[choice.key] === option.value}
                onClick={() => choose(choice.key, option.value)}
              >
                {option.label.startsWith('obs.') ? t(option.label as MessageKey) : option.label}
              </SegmentedOption>
            ))}
          </div>
        </div>
      ))}

      <div className="operator-setting operator-setting-preview">
        <div className="stream-overlay-preview" ref={frameRef} data-testid="stream-overlay-preview">
          {scale > 0 && (
            <iframe
              className="stream-overlay-preview-page"
              src={`/display${query}&preview=1`}
              title={t('obs.previewTitle')}
              tabIndex={-1}
              width={FRAME_WIDTH}
              height={FRAME_HEIGHT}
              style={{ transform: `scale(${scale})` }}
            />
          )}
        </div>
        <p className="operator-setting-hint">{t('obs.previewHint')}</p>
      </div>

      <div className="operator-setting">
        <div className="operator-setting-text">
          <span className="operator-setting-label">{t('obs.link')}</span>
          <p className="operator-setting-hint">{t('obs.linkHint')}</p>
        </div>
        <div className="operator-setting-control stream-overlay-link">
          <input
            ref={linkRef}
            className="input"
            readOnly
            value={link}
            aria-label={t('obs.link')}
            onFocus={event => event.currentTarget.select()}
          />
          <Button variant="secondary" onClick={() => void copyLink()}>
            {copied ? t('obs.copied') : t('obs.copy')}
          </Button>
        </div>
      </div>
    </>
  )
}
