import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, within } from '@testing-library/react'
import App from './App'
import type { AutoMode, OperatorMessage } from '../shared/types'

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

/** The socket's send spy, so tests can assert what the dialog sent. */
function sentMessages() {
  const send = socket?.send as unknown as ReturnType<typeof vi.fn>
  return send.mock.calls.map(call => JSON.parse(call[0] as string))
}

/** Connect the socket and deliver a snapshot with the given auto-approve mode. */
function openWith(autoMode: AutoMode = 'off') {
  render(<App />)
  act(() => {
    socket?.onopen?.()
  })
  push({
    type: 'state_snapshot',
    data: {
      current_display: null,
      pending: [],
      history: [],
      pending_count: 0,
      history_count: 0,
      display_connections: 1,
      operator_connections: 1,
      display_settings: { verse_limit: 3, theme: 'dark' },
      auto_mode: autoMode,
    },
  })
}

/** The gear in the header. */
function gear() {
  return screen.getByRole('button', { name: 'Settings' })
}

/** The compact auto-approve chip on the console. */
function autoChip(name: RegExp) {
  return screen.getByRole('button', { name })
}

function dialog() {
  return screen.getByRole('dialog')
}

function openSettings() {
  act(() => {
    gear().click()
  })
}

/** The auto-approve options, scoped to their group. */
function autoOptions() {
  return within(within(dialog()).getByRole('group', { name: 'Auto-approve mode' }))
}

/** The verse-limit options, scoped so 'All' cannot collide with auto-approve. */
function verseOptions() {
  return within(within(dialog()).getByRole('group', { name: 'Verses shown on display' }))
}

beforeEach(() => {
  window.localStorage.clear()
  vi.stubGlobal('WebSocket', MockWebSocket)
  // jsdom has no matchMedia; the theme hook reads it when the dialog mounts.
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

describe('settings dialog', () => {
  it('starts closed, opens from the gear, and closes from its close button', () => {
    openWith()

    // Preferences are not on the page: only the way in and the mode chip are.
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(screen.queryByRole('group', { name: 'Verses shown on display' })).not.toBeInTheDocument()

    openSettings()

    expect(dialog()).toHaveAccessibleName('Settings')
    expect(
      within(dialog()).getByRole('group', { name: 'Verses shown on display' })
    ).toBeInTheDocument()
    expect(within(dialog()).getByRole('group', { name: 'Auto-approve mode' })).toBeInTheDocument()

    act(() => {
      within(dialog()).getByRole('button', { name: 'Close settings' }).click()
    })

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('closes on Escape and hands focus back to the gear', () => {
    openWith()
    // The keyboard path: the gear has focus when it is pressed.
    act(() => {
      gear().focus()
      gear().click()
    })

    fireEvent.keyDown(dialog(), { key: 'Escape' })

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    // The trip into the dialog does not dump focus at the page top.
    expect(gear()).toHaveFocus()
  })

  it('moves focus into the dialog and keeps Tab inside it', () => {
    openWith()
    openSettings()

    const close = within(dialog()).getByRole('button', { name: 'Close settings' })
    expect(close).toHaveFocus()

    // Shift-Tab from the first control wraps to the last, so the tab order never
    // walks out to the console behind the modal.
    fireEvent.keyDown(dialog(), { key: 'Tab', shiftKey: true })
    expect(dialog().contains(document.activeElement)).toBe(true)

    const options = within(dialog()).getAllByRole('button')
    const last = options[options.length - 1]
    act(() => {
      last.focus()
    })
    fireEvent.keyDown(dialog(), { key: 'Tab' })
    expect(close).toHaveFocus()
  })

  it('closes when the backdrop is clicked, and not when the dialog is', () => {
    openWith()
    openSettings()

    fireEvent.click(dialog())
    expect(screen.getByRole('dialog')).toBeInTheDocument()

    fireEvent.click(dialog().parentElement as HTMLElement)
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })
})

describe('app theme in settings', () => {
  it('applies a stored theme on load, with the dialog never opened', () => {
    window.localStorage.setItem('biblelive-theme', 'dark')
    render(<App />)

    // The control moved into the dialog; applying the stored choice at start-up
    // is the console's job, not the control's.
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('is no longer in the header and still persists the choice', () => {
    openWith()

    expect(screen.queryByRole('radiogroup', { name: 'Theme selection' })).not.toBeInTheDocument()

    openSettings()
    const theme = within(dialog()).getByRole('radiogroup', { name: 'Theme selection' })

    act(() => {
      within(theme).getByRole('radio', { name: 'Dark theme' }).click()
    })

    expect(window.localStorage.getItem('biblelive-theme')).toBe('dark')
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark')
    expect(within(theme).getByRole('radio', { name: 'Dark theme' })).toBeChecked()
  })
})

describe('display preferences in settings', () => {
  it('keeps the operator’s choices when the dialog is closed and reopened', () => {
    openWith()
    openSettings()

    act(() => {
      verseOptions().getByRole('button', { name: '5' }).click()
    })

    expect(sentMessages()).toContainEqual({ action: 'set_verse_limit', verse_limit: 5 })
    expect(window.localStorage.getItem('biblelive_display_verse_limit')).toBe('5')

    act(() => {
      within(dialog()).getByRole('button', { name: 'Close settings' }).click()
    })
    openSettings()

    // Reopening reads the same state back: closing a dialog is not a reset.
    expect(verseOptions().getByRole('button', { name: '5' })).toHaveAttribute(
      'aria-pressed',
      'true'
    )

    act(() => {
      within(dialog())
        .getByRole('group', { name: 'Display background theme' })
        .querySelectorAll('button')[1]
        .click()
    })

    expect(sentMessages()).toContainEqual({ action: 'set_display_theme', theme: 'light' })
    expect(window.localStorage.getItem('biblelive_display_theme')).toBe('light')
  })
})

describe('auto-approve in settings', () => {
  it('opens the dialog on the auto-approve section from the compact chip', () => {
    openWith()
    expect(autoChip(/Auto-approve: Off/)).toBeInTheDocument()

    act(() => {
      autoChip(/Auto-approve: Off/).click()
    })

    // The chip is a shortcut into the section that changes it, not a second
    // copy of the controls — the active mode is already focused there.
    expect(autoOptions().getByRole('button', { name: 'Off' })).toHaveAttribute(
      'aria-pressed',
      'true'
    )
    expect(autoOptions().getByRole('button', { name: 'Off' })).toHaveFocus()
  })

  it('keeps the chip and the dialog on the same mode, with the same semantics', () => {
    openWith()
    openSettings()

    act(() => {
      autoOptions().getByRole('button', { name: 'Med+' }).click()
    })

    // The wire values are unchanged by the move: 'Med+' is still the server's
    // 'medium_and_up'.
    expect(sentMessages()).toContainEqual({ action: 'set_auto_mode', auto_mode: 'medium_and_up' })
    expect(
      within(dialog()).getByText(
        'New high- and medium-confidence detections go straight to the display.'
      )
    ).toBeInTheDocument()

    act(() => {
      within(dialog()).getByRole('button', { name: 'Close settings' }).click()
    })

    // With the settings closed the console still says what the server is doing.
    expect(autoChip(/Auto-approve: Med\+/)).toBeInTheDocument()
    expect(autoChip(/Auto-approve: Med\+/)).toHaveClass('is-on')
  })

  it('follows the mode the server confirms, in both places', () => {
    openWith()
    openSettings()

    push({ type: 'auto_mode_confirmed', data: { auto_mode: 'all' } })

    expect(autoOptions().getByRole('button', { name: 'All' })).toHaveAttribute(
      'aria-pressed',
      'true'
    )

    act(() => {
      within(dialog()).getByRole('button', { name: 'Close settings' }).click()
    })

    expect(autoChip(/Auto-approve: All/)).toBeInTheDocument()
  })

  it('shows the mode the snapshot reported, before anything is touched', () => {
    openWith('high')

    expect(autoChip(/Auto-approve: High/)).toBeInTheDocument()

    openSettings()
    expect(autoOptions().getByRole('button', { name: 'High' })).toHaveAttribute(
      'aria-pressed',
      'true'
    )
  })
})

describe('keyboard shortcuts in settings', () => {
  it('lists every shortcut in the dialog instead of on the main page', () => {
    openWith()

    // The bottom legend is gone: the main page names no shortcut.
    expect(screen.queryByText('Navigate')).not.toBeInTheDocument()
    expect(screen.queryByText('Clear Selection')).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Keyboard Shortcuts' })).not.toBeInTheDocument()

    openSettings()

    const section = within(dialog()).getByRole('heading', { name: 'Keyboard Shortcuts' })
    expect(section).toBeInTheDocument()
    const list = section.closest('section')
    if (!list) throw new Error('shortcuts section not found')
    const text = within(list as HTMLElement)
    // Every supported shortcut, with its actual behavior.
    expect(text.getByText('Move the pending selection')).toBeInTheDocument()
    expect(text.getByText(/Show the staged reference/)).toBeInTheDocument()
    expect(text.getByText(/show the resolved passage directly/)).toBeInTheDocument()
    expect(text.getByText(/close this dialog when it is open/)).toBeInTheDocument()
  })
})
