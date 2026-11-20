import { expect, afterEach, beforeEach, vi } from 'vitest'
import { cleanup } from '@testing-library/react'
import * as matchers from '@testing-library/jest-dom/matchers'

// Extend Vitest's expect with jest-dom matchers
expect.extend(matchers)

// The operator console asks the server for the spoken language as soon as its
// socket opens. A test that is not about that gets a server that never answers,
// so the request cannot resolve into state after the test has moved on; a test
// that is about it (or about any other request) stubs `fetch` itself, which
// replaces this. Each test restores globals when it finishes, so this is set
// again before the next one.
beforeEach(() => {
  vi.stubGlobal('fetch', () => new Promise<Response>(() => {}))
})

// jsdom has no layout and no scrollIntoView. The cursor scrolls the row a keyboard
// move lands on into view, so give it a no-op that tests can spy on. A node-environment
// test (operatorReducer.test.ts) has no Element at all.
if (typeof Element !== 'undefined') Element.prototype.scrollIntoView = vi.fn()

// Cleanup after each test
afterEach(() => {
  cleanup()
})
