/**
 * The idle screen: a picture the audience display shows while nothing is on screen.
 *
 * The server sends it as the optional `idle_image` field of the `display_settings`
 * message (`IdleScreenPicture` in `types.ts`). An older server omits it, which means a
 * blank screen.
 */
import type { IdleScreenPicture } from './types'

/** How the picture fills the screen: `logo` centres it at its own size, `fill` covers the screen. */
export type IdleScreenFit = IdleScreenPicture['fit']

export const IDLE_SCREEN_FITS: readonly IdleScreenFit[] = ['logo', 'fill']

/** The idle picture to show: the version of the upload, and how it fits. */
export type IdleImage = IdleScreenPicture

/**
 * The picture's URL for a version. The version only busts the browser cache, so a new
 * upload is never shown from an old copy; the server ignores it.
 */
export function idleImageUrl(image: { version: string }): string {
  return `/api/idle-screen/image?v=${encodeURIComponent(image.version)}`
}

/**
 * The idle picture from a `display_settings` payload, or null for a blank screen. An
 * absent field (an older server), null, or a malformed value all mean blank.
 */
export function readIdleImage(data: unknown): IdleImage | null {
  if (typeof data !== 'object' || data === null) return null
  const value = (data as { idle_image?: unknown }).idle_image
  if (typeof value !== 'object' || value === null) return null
  const { version, fit } = value as { version?: unknown; fit?: unknown }
  if (typeof version !== 'string' || !/^[0-9a-f]{16}$/.test(version)) return null
  if (fit !== 'logo' && fit !== 'fill') return null
  return { version, fit }
}
