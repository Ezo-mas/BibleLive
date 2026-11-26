import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import type { RefObject } from 'react'
import { useWebSocket } from '../shared/useWebSocket'
import { useI18n } from '../shared/i18nContext'
import { formatReference } from '../shared/reference'
import {
  coerceDisplayTheme,
  coerceVerseLimit,
  readStoredVerseLimit,
  selectVerses,
  shownReference,
  writeStoredDisplayTheme,
} from '../shared/verseText'
import type { DisplayTheme, VerseLimit } from '../shared/verseText'
import { idleImageUrl, readIdleImage } from '../shared/idleScreen'
import { coerceClearMode } from '../shared/clearMode'
import type { IdleImage } from '../shared/idleScreen'
import type { BibleReference, ClearMode, DisplayMessage, VerseText } from '../shared/types'
import { audienceQrForPage } from './audienceQr'
import type { ShownAudienceQr } from './audienceQr'
import { computeFitFontSize, fitFontSizeByMeasurement, MAX_FONT_SIZE } from './fitText'
import { SCREEN_SWAP, swapStyle, VERSE_SWAP } from './fadeTimings'
import type { SwapTiming } from './fadeTimings'
import '../styles/tokens.css'
import '../styles/global.css'
import './display.css'

/** How long the screen takes to fade to black off air, and back on air. */
export const LIVE_FADE_MS = 400

/**
 * The fade ends when its animation does (mounting the stage under the black can delay
 * the start); this timer only finishes it when no animation event comes, as in a
 * background tab, where animations do not run.
 */
export const LIVE_FADE_FALLBACK_MS = 2 * LIVE_FADE_MS

/** Line-height multiplier applied to the fitted verse font size. */
const VERSE_LINE_HEIGHT = 1.35

export default function App() {
  const { t } = useI18n()
  const [reference, setReference] = useState<BibleReference | null>(null)
  // Whether what the stage holds (the passage, or the idle picture) is faded in. It
  // goes false while the old content fades out and true once the new one is in.
  const [visible, setVisible] = useState(true)
  // Presentation-only: the operator console owns this value and the server
  // pushes it here. localStorage is only a pre-socket fallback for the first
  // render, so a reconnect does not flash a stale verse count.
  const [verseLimit, setVerseLimit] = useState<VerseLimit>(() =>
    coerceVerseLimit(readStoredVerseLimit(window.localStorage))
  )
  // Dark by default: a white projector washes out in a lit room. The operator
  // owns the choice and the server pushes it here like the verse limit.
  const [theme, setTheme] = useState<DisplayTheme>(() =>
    document.documentElement.getAttribute('data-theme') === 'light' ? 'light' : 'dark'
  )
  const [fontSize, setFontSize] = useState(MAX_FONT_SIZE)
  // The operator's idle picture. Shown only while nothing is on screen.
  const [idleImage, setIdleImage] = useState<IdleImage | null>(null)
  const [clearMode, setClearMode] = useState<ClearMode>('idle')
  // Off air the page draws nothing. The server also stops sending passages and clears
  // this page, so this is the second lock, not the only one.
  const [live, setLive] = useState(true)
  // The socket handler reads it between renders: back on air the server sends the
  // passage just before the settings, and both must land before the next paint.
  const liveRef = useRef(true)
  // The black cover while the screen fades to black ('in') or back from it ('out').
  // Fully off air the page is drawn black on its own and the cover is gone.
  const [cover, setCover] = useState<'in' | 'out' | null>(null)
  const coverTimerRef = useRef<number | undefined>(undefined)
  // Messages that arrive while the screen fades to black (the server clears right
  // after going off air): applied once it is black, so the fade never shows the
  // stage changing underneath it.
  const fadingToBlackRef = useRef(false)
  const heldMessagesRef = useRef<DisplayMessage[]>([])
  // The first settings after the page loads set live or off air at once: a page that
  // loads while off air goes straight to black instead of fading from the idle picture.
  const settingsSeenRef = useRef(false)
  // Page-local by design: a display reloaded mid-service shows the picture again
  // until the next passage; the protocol has no server-side "has shown" field.
  const [hasShownPassage, setHasShownPassage] = useState(false)
  // The version of a picture the browser could not load (its file is gone, say), so the
  // audience sees a blank screen rather than a broken-image icon. A new upload is a new
  // version and gets a fresh try.
  const [brokenIdleVersion, setBrokenIdleVersion] = useState<string | null>(null)
  // The audience QR code the launcher turned on, if this page draws it (see audienceQr.ts).
  const [audienceQr, setAudienceQr] = useState<ShownAudienceQr | null>(null)
  const swapTimerRef = useRef<number | undefined>(undefined)
  const stageRef = useRef<HTMLDivElement>(null)
  const versesRef = useRef<HTMLDivElement>(null)
  // The current swap's exit and entrance durations: quicker passage to passage than
  // to and from the idle screen (see fadeTimings.ts).
  const [timing, setTiming] = useState<SwapTiming>(SCREEN_SWAP)
  // On (re)connecting the server sends what is on screen (a passage or a clear), then
  // the settings. That first content is not a new show: it goes up at once, so a
  // reloaded page never shows the idle picture fading out first.
  const awaitingInitialStateRef = useRef(true)
  // What is on the stage as of the last render, for the socket handler.
  const shownRef = useRef<{ reference: BibleReference | null; visible: boolean }>({
    reference: null,
    visible: true,
  })

  // Off air nothing is drawn, so there is nothing to fade: take the passage (or the
  // clear) at once, and going back on air shows it without flashing the idle screen.
  const applyAtOnce = (message: DisplayMessage) => {
    window.clearTimeout(swapTimerRef.current)
    if (message.type === 'reference') {
      setReference(message.data)
      setHasShownPassage(true)
    } else if (message.type === 'clear') {
      setReference(null)
    }
    setVisible(true)
  }

  const releaseHeldMessages = () => {
    fadingToBlackRef.current = false
    const held = heldMessagesRef.current
    heldMessagesRef.current = []
    held.forEach(applyAtOnce)
  }

  const goLive = (next: boolean) => {
    const was = liveRef.current
    liveRef.current = next
    const animate = settingsSeenRef.current && was !== next
    settingsSeenRef.current = true
    if (!animate) {
      if (was !== next) {
        window.clearTimeout(coverTimerRef.current)
        releaseHeldMessages()
        setLive(next)
        setCover(null)
      }
      return
    }
    window.clearTimeout(coverTimerRef.current)
    if (!next) {
      // Fade to black over the stage as it is; only then go dark and catch up.
      fadingToBlackRef.current = true
      setCover('in')
    } else {
      // Back on air: anything held from a fade cut short lands first, then the stage
      // is drawn under the black and the black fades away.
      releaseHeldMessages()
      setLive(true)
      setCover('out')
    }
    coverTimerRef.current = window.setTimeout(finishCover, LIVE_FADE_FALLBACK_MS)
  }

  // The cover's fade is over: black now means off air proper, clear means gone.
  const finishCover = () => {
    window.clearTimeout(coverTimerRef.current)
    if (fadingToBlackRef.current) {
      releaseHeldMessages()
      setLive(false)
    }
    setCover(null)
  }

  const handleMessage = (message: DisplayMessage) => {
    if (message.type === 'display_settings') {
      setVerseLimit(coerceVerseLimit(message.data.verse_limit))
      setTheme(coerceDisplayTheme(message.data.theme))
      setClearMode(coerceClearMode(message.data.clear_mode))
      setIdleImage(readIdleImage(message.data))
      awaitingInitialStateRef.current = false
      goLive(message.data.live !== false)
      return
    }

    // Beside the passage, not instead of it: no fade, and nothing else changes.
    if (message.type === 'audience_qr') {
      setAudienceQr(audienceQrForPage(message.data, window.location.origin))
      return
    }

    if (fadingToBlackRef.current) {
      heldMessagesRef.current.push(message)
      return
    }
    if (!liveRef.current || awaitingInitialStateRef.current) {
      applyAtOnce(message)
      return
    }

    if (message.type === 'reference') {
      // A newer reference always wins; cancel any in-flight content swap.
      window.clearTimeout(swapTimerRef.current)
      const next = message.data
      // Fade out whatever is showing, then fade the new reference in: quickly over a
      // passage, a little slower from the idle screen.
      const shown = shownRef.current
      const swap = shown.reference && shown.visible ? VERSE_SWAP : SCREEN_SWAP
      setTiming(swap)
      setVisible(false)
      swapTimerRef.current = window.setTimeout(() => {
        setReference(next)
        setHasShownPassage(true)
        setVisible(true)
      }, swap.exitMs)
    } else if (message.type === 'clear') {
      window.clearTimeout(swapTimerRef.current)
      // 'clear': fade out, then fade the waiting state (the idle picture) in.
      setTiming(SCREEN_SWAP)
      setVisible(false)
      swapTimerRef.current = window.setTimeout(() => {
        setReference(null)
        setVisible(true)
      }, SCREEN_SWAP.exitMs)
    } else {
      const unhandled: never = message
      void unhandled
    }
  }

  // The display must outlive server restarts and network blips: a projector
  // that goes dark mid-service cannot be recovered without manual refresh, so
  // keep retrying indefinitely. The operator also retries indefinitely; its
  // token prompt comes from close code 1008, which stops retries.
  useWebSocket<DisplayMessage>('/ws/display', handleMessage, {
    maxReconnectAttempts: Infinity,
    onClose: () => {
      awaitingInitialStateRef.current = true
    },
  })

  // Cancel a pending swap or live fade when the page goes away.
  useEffect(
    () => () => {
      window.clearTimeout(swapTimerRef.current)
      window.clearTimeout(coverTimerRef.current)
    },
    []
  )

  // The theme lives on <html> so the design tokens resolve for the whole page.
  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
    document.documentElement.style.colorScheme = theme
    // Persisted so a reload paints the right background before the socket
    // delivers the server value.
    writeStoredDisplayTheme(theme, window.localStorage)
  }, [theme])

  // Keyboard shortcut for the operator standing at the projector: T toggles the
  // background. The page stays presentation-only; the server setting still wins
  // on the next display_settings broadcast.
  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 't' && event.key !== 'T') return
      if (event.metaKey || event.ctrlKey || event.altKey) return
      setTheme(current => (current === 'dark' ? 'light' : 'dark'))
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [])

  const visibleVerses = selectVerses(reference, verseLimit)

  // Shrink the font until the visible verses fit the viewport, and re-fit on
  // resize. Projectors have no scrollbar, so overflow is never acceptable.
  useLayoutEffect(() => {
    const stage = stageRef.current
    const block = versesRef.current
    if (!stage || !block || visibleVerses.length === 0) return

    const compute = () => {
      const rect = stage.getBoundingClientRect()
      const estimate = computeFitFontSize({
        charCount: visibleVerses.reduce((sum, v) => sum + v.text.length, 0),
        lineCount: visibleVerses.length,
        widthPx: rect.width,
        heightPx: rect.height,
        lineHeight: VERSE_LINE_HEIGHT,
      })

      const inner = block.querySelector('.display-verses-inner') as HTMLElement | null
      // `block` is stretched to the leftover height, so the overflow signal is
      // the inner wrapper's natural height against the space `block` occupies.
      const apply = (size: number) => {
        block.style.fontSize = `${size}px`
      }
      const fitted = fitFontSizeByMeasurement(
        estimate,
        apply,
        () => inner?.offsetHeight ?? 0,
        block.clientHeight
      )
      apply(fitted)
      setFontSize(current => (current === fitted ? current : fitted))
    }

    compute()
    const observer = new ResizeObserver(compute)
    observer.observe(stage)
    return () => observer.disconnect()
    // `live`: off air the stage is not mounted, so going live must fit again.
    // `audienceQr`: the code takes room beside the stage, so the text fits again.
  }, [visibleVerses, theme, live, audienceQr])

  shownRef.current = { reference, visible }

  const fadeClass = visible ? 'display-fade-in' : 'display-fade-out'
  const idleFadeClass = visible ? 'display-idle--in' : 'display-idle--out'

  if (!live)
    return <div className="display-app display-app--off-air" data-testid="display-off-air" />

  return (
    <div className={`display-app${audienceQr ? ' display-app--with-qr' : ''}`}>
      {/* Measured stage: the verse text is sized to fit exactly this box. */}
      <div className="display-stage" ref={stageRef}>
        {reference ? (
          <div className={`display-content ${fadeClass}`} style={swapStyle(timing)}>
            <PassageBody
              reference={reference}
              verses={visibleVerses}
              fontSize={fontSize}
              versesRef={versesRef}
            />
          </div>
        ) : (
          // Nothing on screen: the themed background, plus the operator's picture if one is set.
          <div className={`display-idle ${idleFadeClass}`} style={swapStyle(timing)}>
            {idleImage &&
              (clearMode === 'idle' || !hasShownPassage) &&
              idleImage.version !== brokenIdleVersion && (
                <img
                  className={`display-idle-image display-idle-image--${idleImage.fit}`}
                  src={idleImageUrl(idleImage)}
                  alt={t('idle.imageAlt')}
                  data-testid="display-idle-image"
                  onError={() => setBrokenIdleVersion(idleImage.version)}
                />
              )}
          </div>
        )}
      </div>

      {/* Off air the page returns early above, so the code goes dark with everything else. */}
      {audienceQr && (
        <figure className="display-audience-qr" data-testid="display-audience-qr">
          <img className="display-audience-qr-code" src={audienceQr.svg} alt={t('display.qrAlt')} />
          <figcaption className="display-audience-qr-caption">
            <span>{t('display.qrCaption')}</span>
            <span className="display-audience-qr-url">{audienceQr.label}</span>
          </figcaption>
        </figure>
      )}

      {/* Fading to black off air, or back from it on air: over everything, QR included. */}
      {cover && (
        <div
          className={`display-off-air-cover display-off-air-cover--${cover}`}
          data-testid="display-off-air-cover"
          aria-hidden="true"
          onAnimationEnd={finishCover}
        />
      )}
    </div>
  )
}

interface PassageBodyProps {
  reference: BibleReference
  verses: VerseText[]
  fontSize: number
  versesRef: RefObject<HTMLDivElement>
}

/** The verses and the citation of one passage; the citation names only the verses shown. */
function PassageBody({ reference, verses, fontSize, versesRef }: PassageBodyProps) {
  return (
    <>
      {/* Verse text - primary focus, bounded to the chosen limit */}
      <div
        className="display-verses"
        ref={versesRef}
        style={{ fontSize: `${fontSize}px` }}
        data-testid="display-verses"
      >
        <div className="display-verses-inner">
          {verses.map(verse => (
            <p className="display-verse" key={verse.verse}>
              <span className="display-verse-number" aria-hidden="true">
                {verse.verse}
              </span>
              <span className="display-verse-text">{verse.text}</span>
            </p>
          ))}
        </div>
      </div>

      {/* Reference - secondary, elegant label below */}
      <div className="display-reference">{formatReference(shownReference(reference, verses))}</div>
    </>
  )
}
