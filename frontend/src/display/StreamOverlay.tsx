import { useState } from 'react'
import { useWebSocket } from '../shared/useWebSocket'
import { coerceVerseLimit, readStoredVerseLimit } from '../shared/verseText'
import type { VerseLimit } from '../shared/verseText'
import type { BibleReference, DisplayMessage } from '../shared/types'
import { parseStreamOverlayOptions } from './streamOverlayOptions'
import { StreamBand } from './StreamBand'
import { useFadeSwap } from './useFadeSwap'

/**
 * The passage on screen as a lower third for a stream: `/display?mode=obs`,
 * added in OBS as a Browser source over the camera. It shows what the projector
 * shows (same messages, same verse limit) on a transparent page.
 */
export default function StreamOverlay() {
  const [options, setOptions] = useState(() => parseStreamOverlayOptions(window.location.search))
  const { shown, visible, timing, swap } = useFadeSwap<BibleReference>()
  const [verseLimit, setVerseLimit] = useState<VerseLimit>(() =>
    coerceVerseLimit(readStoredVerseLimit(window.localStorage))
  )

  const handleMessage = (message: DisplayMessage) => {
    if (message.type === 'display_settings') {
      // The projector's background theme does not apply: the band has its own look.
      // Off air the server also clears the page, so the band fades out with the projector.
      setVerseLimit(coerceVerseLimit(message.data.verse_limit))
      setOptions(parseStreamOverlayOptions(window.location.search, message.data.stream_overlay))
      return
    }
    // The audience QR code is for people in the room, not for the stream.
    if (message.type === 'audience_qr') return
    if (message.type === 'reference' || message.type === 'clear') {
      swap(message.type === 'reference' ? message.data : null)
    } else {
      const unhandled: never = message
      void unhandled
    }
  }

  // Like the projector, the overlay must survive server restarts unattended.
  useWebSocket<DisplayMessage>('/ws/display', handleMessage, {
    maxReconnectAttempts: Infinity,
  })

  return (
    <StreamBand
      reference={shown}
      visible={visible}
      timing={timing}
      verseLimit={verseLimit}
      options={options}
    />
  )
}
