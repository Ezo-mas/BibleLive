import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import App from './App'
import { I18nProvider } from '../shared/I18nProvider'
import { LANGUAGE_STORAGE_KEY } from '../shared/i18n'
import type { BibleReference } from '../shared/types'
import { createMockSocket, push } from '../test/operatorHarness'
import type { MockSocket } from '../test/operatorHarness'

let socket: MockSocket | null = null

/** Constructor stub; `OPEN` is what the shared hook compares readyState to. */
const MockWebSocket = Object.assign(
  function MockWebSocketStub() {
    socket = createMockSocket()
    return socket
  },
  { OPEN: 1, CLOSING: 2, CLOSED: 3 }
)

function reference(id = 'p-1', book = 'John', chapter = 1, verseStart = 1): BibleReference {
  return {
    id,
    book,
    chapter,
    verse_start: verseStart,
    verse_end: null,
    confidence: 'high',
    inferred: false,
    text: `${book} ${chapter} text`,
    verses: [{ verse: verseStart, text: `${book} ${chapter}:${verseStart}` }],
    created_at: '2026-01-01T10:00:00Z',
  }
}

/** Connect the socket and deliver a snapshot with one pending reference. */
function openWith(wrapper?: typeof I18nProvider) {
  render(<App />, { wrapper })
  act(() => {
    socket?.onopen?.()
  })
  push(socket, {
    type: 'state_snapshot',
    data: {
      current_display: null,
      pending: [reference()],
      history: [],
      pending_count: 1,
      history_count: 0,
      display_connections: 1,
      operator_connections: 1,
      display_settings: { verse_limit: 3, theme: 'dark' },
      auto_mode: 'off',
    },
  })
}

/**
 * Open the settings dialog, where the display and auto-approve controls live,
 * and hand back its contents for scoped queries.
 */
function openSettings() {
  act(() => {
    screen.getByRole('button', { name: 'Settings' }).click()
  })
  return within(screen.getByRole('dialog'))
}

/** The verse-limit segmented control, scoped so 'All' cannot collide. */
function versesShown() {
  return within(screen.getByRole('group', { name: 'Verses shown on the display' }))
}

beforeEach(() => {
  window.localStorage.clear()
  vi.stubGlobal('WebSocket', MockWebSocket)
  // jsdom has no matchMedia; ThemeToggle's useTheme hook reads it on mount.
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

/** A server error for `action`, which the console shows as an alert. */
function fail(action: string, message = 'Something went wrong') {
  push(socket, { type: 'error', data: { message, action } })
}

describe('notices', () => {
  it('says nothing when an action succeeds: the console already shows it', () => {
    openWith()

    fireEvent.doubleClick(
      screen.getByRole('button', { name: 'John 1:1 — highlight; Enter shows it' })
    )
    push(socket, { type: 'approval_confirmed', data: { reference: reference() } })
    push(socket, { type: 'clear_confirmed', data: {} })
    push(socket, { type: 'pending_cleared', data: {} })
    push(socket, { type: 'display_settings_confirmed', data: { verse_limit: 5, theme: 'light' } })
    push(socket, { type: 'auto_mode_confirmed', data: { auto_mode: 'high' } })
    push(socket, { type: 'follow_auto_confirmed', data: { follow_auto: true } })

    expect(screen.queryByRole('status')).not.toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('replaces the previous notice instead of stacking a second one', () => {
    openWith()

    fail('approve')
    fail('clear')

    expect(screen.getAllByRole('alert')).toHaveLength(1)
    expect(screen.getByRole('alert')).toHaveTextContent('Could not clear the display')
  })

  it('keeps the notice in the header, clear of the actions and the queue', () => {
    openWith()

    fail('approve')

    // Placement is the fix for a measured problem: as a fixed overlay the notice
    // sat on the action row on a laptop and on the first queue card on a short
    // landscape window, which are the controls the operator reaches for next.
    const notice = screen.getByRole('alert')
    const header = notice.closest('header')
    expect(header).not.toBeNull()
    expect(within(header as HTMLElement).getByText('BibleLive')).toBeInTheDocument()
    expect(notice.closest('.operator-main')).toBeNull()
  })

  it('lets the operator dismiss the notice by hand', () => {
    openWith()
    fail('clear')

    act(() => {
      screen.getByRole('button', { name: /dismiss message/i }).click()
    })

    expect(screen.queryByText(/Could not clear the display/)).not.toBeInTheDocument()
  })
})

describe('action failures', () => {
  it('surfaces a failed approval with the server reason', () => {
    openWith()

    push(socket, {
      type: 'error',
      data: { message: 'Reference not found or already processed', action: 'approve' },
    })

    const alert = screen.getByRole('alert')
    expect(alert).toHaveTextContent('Could not show that reference')
    expect(alert).toHaveTextContent('Reference not found or already processed')
  })

  it('localizes the reason when a detected reference is no longer pending', () => {
    window.localStorage.setItem(LANGUAGE_STORAGE_KEY, 'lt')
    openWith(I18nProvider)

    push(socket, {
      type: 'error',
      data: { message: 'Reference not found or already processed', action: 'approve' },
    })

    expect(screen.getByRole('alert')).toHaveTextContent(
      'Nepavyko rodyti šios nuorodos: Nuorodos nebėra laukiančiųjų sąraše'
    )
  })

  it('reverts a rejected settings change and says so', async () => {
    openWith()
    openSettings()

    act(() => {
      versesShown().getByRole('button', { name: '5' }).click()
    })
    expect(versesShown().getByRole('button', { name: '5' })).toHaveAttribute('aria-pressed', 'true')

    push(socket, {
      type: 'error',
      data: {
        message: "Invalid verse_limit; expected one of 3, 5, 10, 'all'",
        action: 'set_verse_limit',
      },
    })

    // The control must not keep claiming a value the display is not using.
    await waitFor(() =>
      expect(versesShown().getByRole('button', { name: '3' })).toHaveAttribute(
        'aria-pressed',
        'true'
      )
    )
    expect(versesShown().getByRole('button', { name: '5' })).toHaveAttribute(
      'aria-pressed',
      'false'
    )
    expect(screen.getByRole('alert')).toHaveTextContent('reverted')
  })

  it('puts the confirmed verse limit and theme back in storage when the server refuses them', () => {
    openWith()
    const settings = openSettings()

    act(() => {
      versesShown().getByRole('button', { name: '5' }).click()
    })
    expect(window.localStorage.getItem('biblelive_display_verse_limit')).toBe('5')
    push(socket, {
      type: 'error',
      data: {
        message: "Invalid verse_limit; expected one of 3, 5, 10, 'all'",
        action: 'set_verse_limit',
      },
    })
    expect(window.localStorage.getItem('biblelive_display_verse_limit')).toBe('3')

    act(() => {
      within(settings.getByRole('group', { name: 'Display background theme' }))
        .getByRole('button', { name: 'Light' })
        .click()
    })
    expect(window.localStorage.getItem('biblelive_display_theme')).toBe('light')
    push(socket, {
      type: 'error',
      data: { message: 'Invalid display_theme', action: 'set_display_theme' },
    })
    expect(window.localStorage.getItem('biblelive_display_theme')).toBe('dark')
  })

  it('falls back to the confirmed auto-approve mode when the change fails', async () => {
    openWith()
    openSettings()

    act(() => {
      within(screen.getByRole('group', { name: 'Auto-approve mode' }))
        .getByRole('button', { name: 'All' })
        .click()
    })

    push(socket, {
      type: 'error',
      data: { message: "Invalid auto_mode; expected 'off'", action: 'set_auto_mode' },
    })

    await waitFor(() =>
      expect(
        within(screen.getByRole('group', { name: 'Auto-approve mode' })).getByRole('button', {
          name: 'Off',
        })
      ).toHaveAttribute('aria-pressed', 'true')
    )
    expect(screen.getByRole('alert')).toHaveTextContent('Could not change auto-approve')
  })
})

describe('settings confirmations', () => {
  it('applies what the server confirms without announcing it', () => {
    openWith()
    openSettings()

    push(socket, { type: 'display_settings_confirmed', data: { verse_limit: 3, theme: 'dark' } })
    expect(screen.queryByRole('status')).not.toBeInTheDocument()

    push(socket, { type: 'display_settings_confirmed', data: { verse_limit: 5, theme: 'dark' } })

    expect(screen.queryByRole('status')).not.toBeInTheDocument()
    expect(versesShown().getByRole('button', { name: '5' })).toHaveAttribute('aria-pressed', 'true')
  })

  it('explains which detections the active auto-approve mode accepts', () => {
    openWith()
    expect(
      openSettings().getByText('Every new detection waits for your approval.')
    ).toBeInTheDocument()

    push(socket, { type: 'auto_mode_confirmed', data: { auto_mode: 'medium_and_up' } })

    expect(
      screen.getByText('New high- and medium-confidence detections go straight to the display.')
    ).toBeInTheDocument()
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })
})

describe('pending card clarity', () => {
  it('marks the highlighted card and shows when the detection arrived', () => {
    openWith()
    const card = screen.getByRole('button', { name: 'John 1:1 — highlight; Enter shows it' })

    act(() => {
      card.click()
    })

    const row = card.closest('.ref-item') as HTMLElement
    expect(row).toHaveClass('is-selected')
    expect(card).toHaveAttribute('aria-pressed', 'true')
    expect(row).toHaveAttribute('data-confidence', 'high')

    const time = within(row).getByText(/\d{1,2}:\d{2}:\d{2}/)
    expect(time).toHaveAttribute('datetime', '2026-01-01T10:00:00Z')
  })
})
