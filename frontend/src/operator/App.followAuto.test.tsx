import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, render, screen, within } from '@testing-library/react'
import App from './App'
import type { BibleReference, FollowAlongHint, OperatorMessage } from '../shared/types'

/** The subset of WebSocket the operator app uses. */
interface MockSocket {
  onopen: (() => void) | null
  onmessage: ((event: { data: string }) => void) | null
  onclose: ((event: { code: number }) => void) | null
  onerror: ((event: unknown) => void) | null
  readyState: number
  send: ReturnType<typeof vi.fn>
  close: () => void
}

let socket: MockSocket | null = null

function createMockSocket(): MockSocket {
  const instance: MockSocket = {
    onopen: null,
    onmessage: null,
    onclose: null,
    onerror: null,
    readyState: 1,
    send: vi.fn(),
    close: vi.fn(),
  }
  socket = instance
  return instance
}

/** Constructor stub; `OPEN` is what the shared hook compares readyState to. */
const MockWebSocket = Object.assign(
  function MockWebSocketStub() {
    return createMockSocket()
  },
  { OPEN: 1, CLOSING: 2, CLOSED: 3 }
)

function push(message: OperatorMessage) {
  act(() => {
    socket?.onmessage?.({ data: JSON.stringify(message) })
  })
}

/** The socket's send spy, so tests can assert what the console sent. */
function sentMessages() {
  const send = socket?.send as unknown as ReturnType<typeof vi.fn>
  return send.mock.calls.map(call => JSON.parse(call[0] as string))
}

/** A passage the server showed by itself for follow-along: no confidence, never queued. */
function followed(id: string, verses: number[]): BibleReference {
  return {
    id,
    book: 'Luke',
    chapter: 16,
    verse_start: verses[0],
    verse_end: verses[verses.length - 1],
    confidence: null,
    inferred: false,
    text: 'Luke 16 passage',
    verses: verses.map(verse => ({ verse, text: `Luke 16:${verse} text` })),
    source: 'follow',
    created_at: '2026-01-01T00:00:00Z',
  }
}

/** A passage an operator picked by hand, for the contrast. */
function manual(id: string, verses: number[]): BibleReference {
  return { ...followed(id, verses), source: 'manual' }
}

function hintFor(displayId: string): OperatorMessage {
  const hint: FollowAlongHint = {
    book: 'Luke',
    chapter: 16,
    verse_start: 13,
    verse_end: 15,
    display_id: displayId,
  }
  return { type: 'follow_along', data: hint }
}

interface OpenOptions {
  currentDisplay?: BibleReference | null
  history?: BibleReference[]
  followAuto?: boolean
}

/** Connect the console and deliver a snapshot with the given display and switch. */
function openWith({ currentDisplay = null, history = [], followAuto = false }: OpenOptions = {}) {
  render(<App />)
  act(() => {
    socket?.onopen?.()
  })
  push({
    type: 'state_snapshot',
    data: {
      current_display: currentDisplay,
      pending: [],
      history,
      pending_count: 0,
      history_count: history.length,
      display_connections: 1,
      operator_connections: 1,
      display_settings: { verse_limit: 3, theme: 'dark' },
      auto_mode: 'off',
      follow_auto: followAuto,
    },
  })
}

function openSettings() {
  act(() => {
    screen.getByRole('button', { name: 'Settings' }).click()
  })
}

/** The follow-along switch, scoped so its 'On' and 'Off' cannot collide with auto-approve's. */
function followOptions() {
  return within(
    within(screen.getByRole('dialog')).getByRole('group', {
      name: 'Follow the reading automatically',
    })
  )
}

const hintRegion = () => screen.queryByRole('region', { name: 'Follow-along suggestion' })

/** Click a history card, which stages it in Up Next and sends nothing. */
function stageFromHistory(name: RegExp) {
  act(() => {
    screen.getByRole('button', { name }).click()
  })
}

const showButton = () => screen.getByRole('button', { name: 'Show on display' })

beforeEach(() => {
  window.localStorage.clear()
  vi.stubGlobal('WebSocket', MockWebSocket)
  vi.stubGlobal('matchMedia', (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }))
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  socket = null
})

describe('follow-along auto-show switch', () => {
  it('is off by default, and the settings say so', () => {
    openWith()
    openSettings()

    expect(followOptions().getByRole('button', { name: 'Off' })).toHaveAttribute(
      'aria-pressed',
      'true'
    )
    expect(followOptions().getByRole('button', { name: 'On' })).toHaveAttribute(
      'aria-pressed',
      'false'
    )
  })

  it('sends the switch to the server when the operator turns it on', () => {
    openWith()
    openSettings()

    act(() => {
      followOptions().getByRole('button', { name: 'On' }).click()
    })

    expect(sentMessages()).toContainEqual({ action: 'set_follow_auto', follow_auto: true })
    expect(followOptions().getByRole('button', { name: 'On' })).toHaveAttribute(
      'aria-pressed',
      'true'
    )
  })

  it('follows the value the server confirms, and says so', () => {
    openWith()
    openSettings()
    act(() => {
      followOptions().getByRole('button', { name: 'On' }).click()
    })

    push({ type: 'follow_auto_confirmed', data: { follow_auto: true } })
    expect(screen.getByText('Follow the reading automatically: On')).toBeInTheDocument()

    push({ type: 'follow_auto_confirmed', data: { follow_auto: false } })
    expect(followOptions().getByRole('button', { name: 'Off' })).toHaveAttribute(
      'aria-pressed',
      'true'
    )
    expect(screen.getByText('Follow the reading automatically: Off')).toBeInTheDocument()
  })

  it('reverts to the confirmed value when the server refuses the change', () => {
    openWith()
    openSettings()
    act(() => {
      followOptions().getByRole('button', { name: 'On' }).click()
    })
    vi.spyOn(console, 'error').mockImplementation(() => {})

    push({
      type: 'error',
      data: { message: 'Invalid follow_auto; expected true or false', action: 'set_follow_auto' },
    })

    expect(followOptions().getByRole('button', { name: 'Off' })).toHaveAttribute(
      'aria-pressed',
      'true'
    )
    expect(screen.getByText(/Could not change automatic following/)).toBeInTheDocument()
  })

  it('shows the value the snapshot reports, before anything is touched', () => {
    openWith({ followAuto: true })
    openSettings()

    expect(followOptions().getByRole('button', { name: 'On' })).toHaveAttribute(
      'aria-pressed',
      'true'
    )
  })

  it('leaves the auto-approve mode alone', () => {
    openWith()
    openSettings()

    act(() => {
      followOptions().getByRole('button', { name: 'On' }).click()
    })

    expect(sentMessages()).not.toContainEqual(expect.objectContaining({ action: 'set_auto_mode' }))
    expect(
      within(screen.getByRole('dialog')).getByRole('group', { name: 'Auto-approve mode' })
    ).toBeInTheDocument()
    expect(
      within(
        within(screen.getByRole('dialog')).getByRole('group', { name: 'Auto-approve mode' })
      ).getByRole('button', { name: 'Off' })
    ).toHaveAttribute('aria-pressed', 'true')
  })
})

describe('follow-along hint while the switch is on', () => {
  const onScreen = manual('d-1', [10, 11, 12])

  it('keeps the plain label: a hint still listed is one the server left to the operator', () => {
    openWith({ currentDisplay: onScreen, followAuto: true })
    push(hintFor('d-1'))

    expect(screen.getByText('Reading now')).toBeInTheDocument()
    expect(screen.getByText('Luke 16:13-15')).toBeInTheDocument()
  })

  it('keeps the hint when the server turns the switch on mid-service', () => {
    openWith({ currentDisplay: onScreen })
    push(hintFor('d-1'))

    push({ type: 'follow_auto_confirmed', data: { follow_auto: true } })

    expect(hintRegion()).toBeInTheDocument()
    expect(screen.getByText('Reading now')).toBeInTheDocument()
  })
})

describe('auto-followed passages', () => {
  it('tags the passage on the live stage', () => {
    const shown = followed('f-1', [13, 14, 15])
    openWith({ currentDisplay: shown, history: [shown] })

    const stage = screen.getByTestId('live-stage')
    expect(within(stage).getByText('Auto-followed')).toBeInTheDocument()
  })

  it('tags its history entry instead of calling it manual', () => {
    const shown = followed('f-1', [13, 14, 15])
    openWith({ currentDisplay: shown, history: [shown] })

    const card = screen.getByRole('button', { name: /Stage Luke 16:13-15 from history/ })
    expect(within(card).getByText('Auto-followed')).toBeInTheDocument()
    expect(within(card).queryByText('Manual')).not.toBeInTheDocument()
  })

  it('leaves a manually shown passage untagged', () => {
    const shown = manual('m-1', [13, 14, 15])
    openWith({ currentDisplay: shown, history: [shown] })

    expect(screen.queryByText('Auto-followed')).not.toBeInTheDocument()
    expect(screen.getByTestId('live-stage')).toBeInTheDocument()
  })

  it('announces a followed passage as followed, not as a manual show', () => {
    openWith()

    push({ type: 'display_confirmed', data: { reference: followed('f-1', [13, 14, 15]) } })

    expect(screen.getByText(/Shown: Luke 16:13-15 \(followed automatically\)/)).toBeInTheDocument()
  })

  it('never offers a followed passage for auto-approval or confidence', () => {
    const shown = followed('f-1', [13, 14, 15])
    openWith({ currentDisplay: shown, history: [shown] })

    const card = screen.getByRole('button', { name: /Stage Luke 16:13-15 from history/ })
    // No detection confidence on the card, so nothing reads it as a detection.
    expect(card).toHaveAttribute('data-confidence', 'manual')
    expect(card.querySelector('.ref-card-confidence')).toBeNull()
  })
})

describe('an auto-followed passage and a Show from this console', () => {
  it('keeps the Show in flight until the confirmation for its own passage arrives', () => {
    const ownChoice = manual('m-1', [20, 21])
    const nextUp = manual('m-2', [22])
    openWith({ history: [nextUp, ownChoice] })

    stageFromHistory(/Stage Luke 16:20-21 from history/)
    act(() => {
      showButton().click()
    })
    expect(sentMessages()).toContainEqual({
      action: 'display_reference',
      book: 'Luke',
      chapter: 16,
      verse_start: 20,
      verse_end: 21,
    })
    // A second passage is staged while the first request is out. Once the follow
    // confirmation has come in, Show must still be disabled although Up Next has a passage.
    stageFromHistory(/Stage Luke 16:22 from history/)
    expect(showButton()).toBeDisabled()

    push({ type: 'display_confirmed', data: { reference: followed('f-1', [13, 14, 15]) } })

    // The follow confirmation answers no Show of this console: it is still pending.
    expect(showButton()).toBeDisabled()
    expect(screen.getByTestId('next-preview')).toHaveTextContent('Luke 16:22')

    push({ type: 'display_confirmed', data: { reference: manual('m-3', [20, 21]) } })

    // Its own confirmation ends the Show, so Show is available for what is staged next.
    expect(showButton()).toBeEnabled()
    expect(screen.getByTestId('next-preview')).toHaveTextContent('Luke 16:22')
  })

  it('leaves a staged passage in place when the follow confirmation has its coordinates', () => {
    openWith({ history: [manual('m-1', [13, 14, 15])] })
    stageFromHistory(/Stage Luke 16:13-15 from history/)

    push({ type: 'display_confirmed', data: { reference: followed('f-1', [13, 14, 15]) } })

    // This console did not ask for the followed passage, so what it staged stays staged.
    expect(screen.getByTestId('next-preview')).toHaveTextContent('Luke 16:13-15')
    expect(screen.queryByText('Nothing staged')).not.toBeInTheDocument()
    expect(screen.getByText('History', { selector: '.operator-next-badge' })).toBeInTheDocument()
  })
})
