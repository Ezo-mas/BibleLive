import { describe, expect, it } from 'vitest'
import { idleImageUrl, readIdleImage } from './idleScreen'

const VERSION = '0123456789abcdef'

describe('readIdleImage', () => {
  it('reads a picture the server sends with the display settings', () => {
    expect(
      readIdleImage({
        verse_limit: 3,
        theme: 'dark',
        idle_image: { version: VERSION, fit: 'fill' },
      })
    ).toEqual({ version: VERSION, fit: 'fill' })
  })

  it.each([
    ['no field (an older server)', { verse_limit: 3, theme: 'dark' }],
    ['null', { verse_limit: 3, theme: 'dark', idle_image: null }],
    ['a string', { verse_limit: 3, theme: 'dark', idle_image: 'idle_screen.png' }],
    ['an array', { idle_image: [VERSION, 'logo'] }],
    ['an unknown fit', { idle_image: { version: VERSION, fit: 'stretch' } }],
    ['a version that is not a 16-digit hex tag', { idle_image: { version: 'v1', fit: 'logo' } }],
    ['a path where the version should be', { idle_image: { version: '../x', fit: 'logo' } }],
    ['no data at all', undefined],
  ])('is null for %s', (_label, data) => {
    expect(readIdleImage(data)).toBeNull()
  })
})

describe('idleImageUrl', () => {
  it('names the picture and carries the version as a cache-buster', () => {
    expect(idleImageUrl({ version: VERSION })).toBe(`/api/idle-screen/image?v=${VERSION}`)
  })
})
