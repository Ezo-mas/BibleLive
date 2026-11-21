import type { AudienceQr } from '../shared/types'

/** The code as this page shows it, or null when it draws none. */
export interface ShownAudienceQr {
  url: string
  svg: string
  /** The address without its scheme, short enough to read off the screen. */
  label: string
}

const SVG_DATA_URI = 'data:image/svg+xml'

function originOf(url: string): string | null {
  try {
    return new URL(url).origin
  } catch {
    return null
  }
}

/**
 * Whether this display page draws the audience QR code.
 *
 * A page opened through the address the code encodes is on a phone that scanned it (or
 * typed it) and draws none: the code is for the projector, and on a phone it would only
 * cover the passage. Anything that is not a well-formed address and SVG is ignored.
 */
export function audienceQrForPage(qr: AudienceQr, pageOrigin: string): ShownAudienceQr | null {
  const { url, svg } = qr
  if (!url || !svg || !svg.startsWith(SVG_DATA_URI)) return null
  const origin = originOf(url)
  if (origin === null || origin === pageOrigin) return null
  return { url, svg, label: url.replace(/^https?:\/\//, '') }
}
