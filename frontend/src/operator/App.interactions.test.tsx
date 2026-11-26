import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import App from './App'
import type { BibleReference, OperatorMessage } from '../shared/types'
import { createMockSocket, lastSent, push } from '../test/operatorHarness'
import type { MockSocket } from '../test/operatorHarness'

let socket: MockSocket | null = null

/**
 * Constructor stub for `WebSocket`. `OPEN` matters: the shared hook only sends
 * while `readyState === WebSocket.OPEN`, so a mock without that static swallows
 * every action silently.
 */
const MockWebSocket = Object.assign(
  function MockWebSocketStub() {
    socket = createMockSocket()
    return socket
  },
  { OPEN: 1, CLOSING: 2, CLOSED: 3 }
)

function reference(
  id: string,
  book: string,
  chapter: number,
  verseStart: number,
  verseEnd: number | null = null
): BibleReference {
  return {
    id,
    book,
    chapter,
    verse_start: verseStart,
    verse_end: verseEnd,
    confidence: 'high',
    inferred: false,
    text: `${book} ${chapter} text`,
    verses: [{ verse: verseStart, text: `${book} ${chapter}:${verseStart}` }],
    created_at: '2026-01-01T00:00:00Z',
  }
}

const pending = [
  reference('p-1', 'John', 1, 1),
  reference('p-2', 'Romans', 8, 28),
  reference('p-3', 'Luke', 1, 5),
]

/** Connect the socket and deliver a snapshot with the standard pending queue. */
function openWith(overrides: Partial<OperatorMessage & { type: 'state_snapshot' }> = {}) {
  render(<App />)
  act(() => {
    socket?.onopen?.()
  })
  push(socket, {
    type: 'state_snapshot',
    data: {
      current_display: null,
      pending,
      history: [],
      pending_count: pending.length,
      history_count: 0,
      display_connections: 1,
      operator_connections: 1,
      display_settings: { verse_limit: 3, theme: 'dark' },
      ...overrides,
    },
  })
}

function press(key: string) {
  act(() => {
    window.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true }))
  })
}

/** The title of the highlighted row (the keyboard cursor), or null when there is none. */
function highlighted() {
  return document.querySelector('.ref-item.is-selected h3')?.textContent ?? null
}

/** A pending row's handle, by its reference. */
function card(ref: string) {
  return screen.getByRole('button', { name: `${ref} — highlight; Enter shows it` })
}

/**
 * Open the settings dialog. Display, theme, and auto-approve preferences live
 * there now, so a test of them is a test of the gear, the dialog, and the
 * controls inside it.
 */
function openSettings() {
  act(() => {
    screen.getByRole('button', { name: 'Settings' }).click()
  })
}

/** Operator actions sent after the authenticate first-message (empty: no token). */
function sentActions() {
  const calls = socket?.send.mock.calls ?? []
  return calls
    .map(([text]) => JSON.parse(text))
    .filter(message => message.action !== 'authenticate')
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

describe('pending highlight and approval', () => {
  it('ArrowDown moves the highlight and focus row by row, scrolls that row into view, and Enter approves it by ID', () => {
    const scroll = vi.mocked(Element.prototype.scrollIntoView)
    scroll.mockClear()
    openWith()
    expect(highlighted()).toBeNull()

    press('ArrowDown')
    expect(card('John 1:1')).toHaveFocus()
    press('ArrowDown')

    // Three rows, so a jump to the last row cannot pass for the second.
    const second = card('Romans 8:28')
    expect(highlighted()).toBe('Romans 8:28')
    expect(second).toHaveAttribute('aria-pressed', 'true')
    expect(second).toHaveFocus()
    expect(scroll).toHaveBeenLastCalledWith({ block: 'nearest' })
    const secondRow = second.closest('[data-cursor-row="p-2"]')
    expect(secondRow).not.toBeNull()
    expect(scroll.mock.contexts.at(-1)).toBe(secondRow)

    // Focus and the cursor stay together, so Enter shows the row that has focus.
    press('Enter')
    expect(sentActions()).toEqual([{ action: 'approve', reference_id: 'p-2' }])
  })

  it('highlights the first item when arrowing up or down from no highlight', () => {
    openWith()
    press('ArrowUp')
    press('Enter')
    expect(lastSent(socket)).toEqual({ action: 'approve', reference_id: 'p-1' })
  })

  it('drops the highlight on Escape and stops approving', () => {
    openWith()
    press('ArrowDown')
    expect(highlighted()).toBe('John 1:1')

    press('Escape')
    expect(highlighted()).toBeNull()

    press('Enter')
    expect(sentActions()).toEqual([])
  })

  it('highlights a pending card by clicking it', () => {
    openWith()
    act(() => {
      card('Romans 8:28').click()
    })
    expect(card('Romans 8:28')).toHaveAttribute('aria-pressed', 'true')
    expect(sentActions()).toEqual([])

    press('Enter')
    expect(lastSent(socket)).toEqual({ action: 'approve', reference_id: 'p-2' })
  })

  it('keeps the highlight when a queue update still holds it', () => {
    openWith()
    press('ArrowDown')

    // A detection arriving mid-decision must not snatch the highlight away.
    // Approval goes by ID, so keeping it cannot show the wrong passage.
    const grown = [reference('p-4', 'Acts', 2, 1), ...pending]
    push(socket, { type: 'pending_update', data: { pending: grown, count: grown.length } })

    expect(highlighted()).toBe('John 1:1')

    press('Enter')
    expect(lastSent(socket)).toEqual({ action: 'approve', reference_id: 'p-1' })
  })

  it('a pending_update that replaces the whole queue drops the highlight, and Enter sends nothing', () => {
    openWith()
    press('ArrowDown')
    press('ArrowDown')
    expect(highlighted()).toBe('Romans 8:28')

    push(socket, {
      type: 'pending_update',
      data: { pending: [reference('p-9', 'Acts', 2, 1)], count: 1 },
    })
    expect(document.querySelector('.ref-item.is-selected')).toBeNull()
    expect(card('Acts 2:1')).toHaveAttribute('aria-pressed', 'false')

    press('Enter')
    expect(sentActions()).toEqual([])
  })
})

describe('pending row buttons', () => {
  it('double-clicking Remove does not approve the row', () => {
    openWith()

    fireEvent.doubleClick(screen.getByRole('button', { name: 'Remove Romans 8:28 from the queue' }))
    expect(sentActions()).toEqual([])
  })

  it('shows no confidence word: a row is told apart by its edge, only when Low', () => {
    openWith()
    const row = card('John 1:1').closest('.ref-item')

    expect(row?.querySelector('.ref-card-confidence')).toBeNull()
    expect(within(row as HTMLElement).queryByText('High')).not.toBeInTheDocument()
    expect(row).toHaveAttribute('data-confidence', 'high')
  })
})

describe('display card controls', () => {
  it('offers the verse limit in Settings and sends the existing action', () => {
    openWith()
    expect(
      screen.queryByRole('group', { name: 'Verses shown on the display' })
    ).not.toBeInTheDocument()
    openSettings()
    const group = within(screen.getByRole('group', { name: 'Verses shown on the display' }))

    act(() => {
      group.getByRole('button', { name: '10' }).click()
    })

    expect(lastSent(socket)).toEqual({ action: 'set_verse_limit', verse_limit: 10 })
    expect(group.getByRole('button', { name: '10' })).toHaveAttribute('aria-pressed', 'true')
  })

  it('puts Clear beside the single live toggle in the card header', () => {
    openWith()
    const clear = screen.getByRole('button', { name: 'Clear display' })
    const toggle = screen.getByRole('button', { name: 'Live' })
    expect(clear).toHaveTextContent(/^Clear$/)
    expect(clear).toHaveClass('btn-subtle', 'btn-sm')
    expect(clear.nextElementSibling).toBe(toggle)
    expect(toggle.closest('.operator-card-head')).toContainElement(
      screen.getByRole('heading', { name: 'On display' })
    )
  })
})

describe('keyboard shortcuts while Settings is open', () => {
  it('does not approve the highlighted reference on Enter until Settings is closed', () => {
    openWith()
    press('ArrowDown')
    expect(highlighted()).toBe('John 1:1')

    openSettings()

    // The dialog owns the keyboard while it is open: a keypress sent from
    // behind it must neither reach the server nor move the highlight.
    press('Enter')
    expect(sentActions()).toEqual([])
    expect(card('John 1:1')).toHaveAttribute('aria-pressed', 'true')

    // Closed, the same keypress approves the highlighted reference by ID — the
    // dialog stood the shortcut down, it did not break it.
    act(() => {
      within(screen.getByRole('dialog')).getByRole('button', { name: 'Close settings' }).click()
    })
    press('Enter')
    expect(lastSent(socket)).toEqual({ action: 'approve', reference_id: 'p-1' })
  })
})

describe('clear actions', () => {
  it('enables Clear Display only while the display has content', () => {
    openWith()
    const clearDisplay = screen.getByRole('button', { name: /clear display/i })
    expect(clearDisplay).toBeDisabled()

    push(socket, {
      type: 'approval_confirmed',
      data: { reference: reference('p-1', 'John', 1, 1) },
    })
    expect(clearDisplay).toBeEnabled()

    act(() => {
      clearDisplay.click()
    })
    expect(lastSent(socket)).toEqual({ action: 'clear' })

    push(socket, { type: 'clear_confirmed', data: {} })
    expect(clearDisplay).toBeDisabled()
  })

  it('clears the queue only after the operator confirms', () => {
    openWith()
    const options = screen.getByRole('button', { name: 'Pending options' })
    function clearQueue() {
      fireEvent.click(options)
      fireEvent.click(screen.getByRole('menuitem', { name: 'Clear queue' }))
    }
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)

    clearQueue()
    expect(confirm).toHaveBeenCalled()
    expect(sentActions()).toEqual([])

    confirm.mockReturnValue(true)
    clearQueue()
    expect(lastSent(socket)).toEqual({ action: 'clear_pending' })
  })
})

describe('B clears the display', () => {
  function pressB(target: EventTarget = window, init: KeyboardEventInit = {}) {
    const event = new KeyboardEvent('keydown', {
      key: 'b',
      code: 'KeyB',
      bubbles: true,
      cancelable: true,
      ...init,
    })
    act(() => {
      target.dispatchEvent(event)
    })
    return event
  }

  function openShowing() {
    openWith()
    push(socket, {
      type: 'approval_confirmed',
      data: { reference: reference('p-1', 'John', 1, 1) },
    })
  }

  it('clears what is on the display, with or without Shift or Caps Lock', () => {
    openShowing()

    const event = pressB()
    expect(lastSent(socket)).toEqual({ action: 'clear' })
    expect(event.defaultPrevented).toBe(true)

    pressB(window, { key: 'B', shiftKey: true })
    expect(sentActions()).toEqual([{ action: 'clear' }, { action: 'clear' }])
  })

  it('works with a button focused', () => {
    openShowing()
    const button = screen.getByRole('button', { name: /clear display/i })
    button.focus()

    pressB(button)

    expect(lastSent(socket)).toEqual({ action: 'clear' })
  })

  it('does nothing when nothing is on the display', () => {
    openWith()

    const event = pressB()

    expect(sentActions()).toEqual([])
    expect(event.defaultPrevented).toBe(false)
  })

  it('types the letter in a text field instead of clearing', () => {
    openShowing()
    const search = document.querySelector('.operator-search input') as HTMLInputElement
    search.focus()

    const event = pressB(search)

    expect(sentActions()).toEqual([])
    expect(event.defaultPrevented).toBe(false)
  })

  it('ignores Ctrl+B, Cmd+B, a held key and an open Settings dialog', () => {
    openShowing()

    pressB(window, { ctrlKey: true })
    pressB(window, { metaKey: true })
    pressB(window, { repeat: true })
    openSettings()
    pressB()

    expect(sentActions()).toEqual([])
  })
})

describe('display settings', () => {
  it('switches the verse limit and background optimistically', async () => {
    openWith()
    openSettings()
    const dialog = within(screen.getByRole('dialog'))
    const five = within(
      screen.getByRole('group', { name: 'Verses shown on the display' })
    ).getByRole('button', { name: '5' })
    act(() => {
      five.click()
    })
    expect(lastSent(socket)).toEqual({ action: 'set_verse_limit', verse_limit: 5 })
    await waitFor(() => expect(five).toHaveAttribute('aria-pressed', 'true'))

    const light = dialog.getByRole('button', { name: 'Light' })
    act(() => {
      light.click()
    })
    expect(lastSent(socket)).toEqual({ action: 'set_display_theme', theme: 'light' })
    await waitFor(() => expect(light).toHaveAttribute('aria-pressed', 'true'))
  })

  it('disables settings controls while disconnected', () => {
    render(<App />)
    openSettings()
    // 'All' also labels an auto-approve option, so scope to the verse-limit group.
    const verseLimits = within(screen.getByRole('group', { name: 'Verses shown on the display' }))
    expect(verseLimits.getByRole('button', { name: '5' })).toBeDisabled()
    expect(verseLimits.getByRole('button', { name: 'All' })).toBeDisabled()

    const autoModes = within(screen.getByRole('group', { name: 'Auto-approve mode' }))
    expect(autoModes.getByRole('button', { name: 'High' })).toBeDisabled()
  })
})

describe('authentication', () => {
  it('prompts for a token, without the failure text, when none was tried', () => {
    render(<App />)
    act(() => {
      socket?.onclose?.({ code: 1008 })
    })
    // A 1008 with no token tried means the server wants one, not that one
    // failed: the prompt opens but there is nothing to call failed.
    expect(screen.queryByText(/authentication failed/i)).not.toBeInTheDocument()
    expect(screen.getByLabelText(/operator token/i)).toBeInTheDocument()
  })

  it('connects with the submitted token and clears the prompt', () => {
    render(<App />)
    act(() => {
      socket?.onclose?.({ code: 1008 })
    })
    const input = screen.getByLabelText(/operator token/i)
    fireEvent.change(input, { target: { value: 'secret' } })
    act(() => {
      screen.getByRole('button', { name: /connect/i }).click()
    })
    expect(window.localStorage.getItem('biblelive_operator_token')).toBe('secret')
    expect(screen.queryByLabelText(/operator token/i)).not.toBeInTheDocument()
  })
})
