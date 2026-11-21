import { describe, expect, it } from 'vitest'
import { audienceQrForPage } from './audienceQr'

const svg = 'data:image/svg+xml;charset=utf-8,%3Csvg%3E%3C%2Fsvg%3E'
const url = 'http://192.168.1.23:8000/display'

describe('audienceQrForPage', () => {
  it('shows the code on the projector, opened from this computer', () => {
    expect(audienceQrForPage({ url, svg }, 'http://127.0.0.1:8000')).toEqual({
      url,
      svg,
      label: '192.168.1.23:8000/display',
    })
  })

  it('draws no code on a page opened through the address it encodes', () => {
    expect(audienceQrForPage({ url, svg }, 'http://192.168.1.23:8000')).toBeNull()
  })

  it('draws nothing when the code is hidden', () => {
    expect(audienceQrForPage({ url: null, svg: null }, 'http://127.0.0.1:8000')).toBeNull()
  })

  it.each([
    ['an address that is not a URL', 'not a url', svg],
    ['an image that is not an SVG data URI', url, 'https://example.com/qr.svg'],
    ['a missing image', url, null],
  ])('ignores %s', (_case, badUrl, badSvg) => {
    expect(audienceQrForPage({ url: badUrl, svg: badSvg }, 'http://127.0.0.1:8000')).toBeNull()
  })
})
