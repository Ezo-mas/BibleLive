import { afterEach, describe, expect, it } from 'vitest'
import { takeOperatorTokenFragment } from './operatorToken'

afterEach(() => {
  window.history.replaceState(null, '', '/')
})

/** The address as the page sees it, path and query included. */
function address() {
  return window.location.pathname + window.location.search + window.location.hash
}

describe('takeOperatorTokenFragment', () => {
  it('returns the fragment token and removes the fragment from the address', () => {
    window.history.replaceState(null, '', '/operator?view=console#token=abc234')

    expect(takeOperatorTokenFragment()).toBe('abc234')
    expect(address()).toBe('/operator?view=console')
  })

  it('decodes a percent-encoded token and trims it like a typed one', () => {
    window.history.replaceState(null, '', '/operator#token=%20a%2Bb%20')

    expect(takeOperatorTokenFragment()).toBe('a+b')
    expect(address()).toBe('/operator')
  })

  it('returns null and removes an empty token fragment', () => {
    window.history.replaceState(null, '', '/operator#token=')

    expect(takeOperatorTokenFragment()).toBeNull()
    expect(address()).toBe('/operator')
  })

  it('returns null and leaves other fragments alone when there is no token', () => {
    window.history.replaceState(null, '', '/operator#follow')

    expect(takeOperatorTokenFragment()).toBeNull()
    expect(address()).toBe('/operator#follow')
  })

  it('returns null when the address has no fragment', () => {
    window.history.replaceState(null, '', '/operator')

    expect(takeOperatorTokenFragment()).toBeNull()
    expect(address()).toBe('/operator')
  })
})
