// @vitest-environment node
import { describe, expect, it } from 'vitest'
import type { BibleReference, OperatorMessage } from '../../shared/types'
import { createOperatorState, operatorReducer } from './operatorReducer'
import type {
  OperatorEvent,
  OperatorReducerState,
  OperatorSettings,
  OptimisticSettingAction,
  RowCursor,
} from './operatorTypes'

const fixtures = import.meta.glob<OperatorMessage>(
  '../../../../backend/tests/fixtures/protocol/operator/*.json',
  { eager: true, import: 'default' }
)

function fixture<T extends OperatorMessage['type']>(type: T) {
  const message = Object.values(fixtures).find(message => message.type === type)
  if (!message) throw new Error(`Missing operator fixture: ${type}`)
  return message as Extract<OperatorMessage, { type: T }>
}

const queued = fixture('pending_update').data.pending[0]
const manual = fixture('display_confirmed').data.reference

function freeze<T>(value: T): T {
  if (value && typeof value === 'object') {
    Object.freeze(value)
    for (const child of Object.values(value)) freeze(child)
  }
  return value
}

function reduce(state: OperatorReducerState, event: OperatorEvent) {
  freeze(state)
  freeze(event)
  const result = operatorReducer(state, event)
  // Same inputs always produce the same result, without mutating either input.
  expect(operatorReducer(state, event)).toEqual(result)
  return result
}

function populatedState(): OperatorReducerState {
  return {
    ...createOperatorState(),
    pending: [queued],
    pendingCount: 1,
    history: [manual],
    historyCount: 7,
    currentDisplay: manual,
    seenPassage: true,
    cursor: { list: 'pending', id: queued.id },
    showInFlight: true,
    followAlongHint: fixture('follow_along').data,
  }
}

describe('operator protocol transitions', () => {
  it.each(Object.entries(fixtures))('handles %s without rendering', (path, message) => {
    expect(path.endsWith(`/${message.type}.json`)).toBe(true)
    const before = populatedState()
    const { state, effects } = reduce(before, message)
    switch (message.type) {
      case 'state_snapshot':
        expect(state).toMatchObject({
          pending: [],
          history: [],
          pendingCount: 0,
          historyCount: 0,
          currentDisplay: null,
          followAlongHint: null,
          displayConnected: false,
          seenPassage: true,
          // The highlighted detection is no longer queued.
          cursor: null,
          showInFlight: true,
        })
        expect(state.settings).toEqual(state.confirmed)
        expect(effects).toEqual([])
        break
      case 'pending_update':
        expect(state.pending).toEqual(message.data.pending)
        expect(state.pendingCount).toBe(message.data.count)
        expect(state.cursor).toEqual(before.cursor)
        expect(effects).toEqual([])
        break
      case 'approval_confirmed':
      case 'display_confirmed':
        expect(state.currentDisplay).toEqual(message.data.reference)
        expect(state.history[0]).toEqual(message.data.reference)
        expect(state.historyCount).toBe(8)
        expect(state.seenPassage).toBe(true)
        expect(state.showInFlight).toBe(false)
        // Showing something never moves the cursor.
        expect(state.cursor).toBe(before.cursor)
        expect(effects).toEqual([
          ...(message.type === 'approval_confirmed' ? [{ type: 'flash', target: 'approve' }] : []),
          {
            type: 'notice',
            tone: 'success',
            message: {
              key: message.type === 'approval_confirmed' ? 'notice.shown' : 'notice.shownManual',
              params: { ref: message.type === 'approval_confirmed' ? 'John 3:16' : 'John 3:16-17' },
            },
          },
        ])
        break
      case 'follow_along':
        expect(state.followAlongHint).toEqual(message.data)
        expect(state.showInFlight).toBe(true)
        expect(effects).toEqual([])
        break
      case 'clear_confirmed':
        expect(state.currentDisplay).toBeNull()
        expect(state.followAlongHint).toBeNull()
        expect(state.seenPassage).toBe(true)
        expect(effects).toEqual([
          { type: 'flash', target: 'clear' },
          { type: 'notice', tone: 'info', message: { key: 'notice.displayCleared' } },
        ])
        break
      case 'pending_cleared':
        // The subsequent pending_update owns the queue and selection changes.
        expect(state).toBe(before)
        expect(effects).toEqual([
          { type: 'flash', target: 'clearPending' },
          { type: 'notice', tone: 'info', message: { key: 'notice.queueCleared' } },
        ])
        break
      case 'connection_status':
        expect(state.displayConnected).toBe(true)
        expect(effects).toEqual([])
        break
      case 'display_settings_confirmed':
        expect(state.settings).toMatchObject({ verseLimit: 5, theme: 'light', clearMode: 'idle' })
        expect(state.confirmed).toEqual(state.settings)
        expect(state.idleImage).toBeNull()
        expect(effects).toEqual([
          {
            type: 'notice',
            tone: 'info',
            message: {
              key: 'notice.saved',
              params: {
                changes: {
                  join: [
                    { key: 'notice.changeVerses', params: { value: 5 } },
                    {
                      key: 'notice.changeBackground',
                      params: { value: { key: 'settings.bgLight' } },
                    },
                  ],
                },
              },
            },
          },
        ])
        break
      case 'auto_mode_confirmed':
        expect(state.settings.autoMode).toBe('high')
        expect(state.confirmed.autoMode).toBe('high')
        expect(effects).toEqual([
          {
            type: 'notice',
            tone: 'info',
            message: {
              key: 'notice.autoApprove',
              params: { mode: { key: 'auto.high' } },
            },
          },
        ])
        break
      case 'follow_auto_confirmed':
        expect(state.settings.followAuto).toBe(true)
        expect(state.confirmed.followAuto).toBe(true)
        expect(effects).toEqual([
          {
            type: 'notice',
            tone: 'info',
            message: {
              key: 'notice.followAuto',
              params: { state: { key: 'follow.on' } },
            },
          },
        ])
        break
      case 'bible_version_confirmed':
        expect(state).toBe(before)
        expect(effects).toEqual([{ type: 'bible_version', data: message.data }])
        break
      case 'error':
        expect(state).toBe(before)
        expect(effects).toEqual([
          { type: 'log_error', reason: message.data.message },
          {
            type: 'notice',
            tone: 'error',
            message: {
              key: 'notice.failed',
              params: {
                prefix: { key: 'notice.errGeneric' },
                reason: message.data.message,
              },
            },
          },
        ])
        break
      default: {
        const unhandled: never = message
        void unhandled
      }
    }
  })
})

describe('the cursor across server updates', () => {
  it.each([
    { cursor: { list: 'pending', id: queued.id }, pending: [queued], kept: true },
    { cursor: { list: 'pending', id: queued.id }, pending: [], kept: false },
    { cursor: { list: 'plan', id: 'plan-1' }, pending: [], kept: true },
  ] satisfies { cursor: RowCursor; pending: BibleReference[]; kept: boolean }[])(
    'snapshot keeps a $cursor.list cursor when kept=$kept',
    ({ cursor, pending, kept }) => {
      const snapshot = fixture('state_snapshot')
      const before = { ...populatedState(), cursor }
      const { state, effects } = reduce(before, {
        ...snapshot,
        data: { ...snapshot.data, pending, pending_count: pending.length },
      })
      expect(state.cursor).toEqual(kept ? cursor : null)
      expect(state.followAlongHint).toBeNull()
      expect(effects).toEqual([])
    }
  )

  it('a queue update never moves the cursor to a new detection', () => {
    const arrived = { ...queued, id: 'arrived' }
    const update = { ...fixture('pending_update'), data: { pending: [arrived, queued], count: 2 } }
    expect(reduce(populatedState(), update).state.cursor).toEqual({
      list: 'pending',
      id: queued.id,
    })
    const none = { ...populatedState(), cursor: null }
    expect(reduce(none, update).state.cursor).toBeNull()
  })

  it('does not let an automatically followed passage settle the operator request', () => {
    const before = populatedState()
    const followed = { ...manual, source: 'follow' as const }
    const { state, effects } = reduce(before, {
      ...fixture('display_confirmed'),
      data: { reference: followed },
    })
    expect(state.cursor).toBe(before.cursor)
    expect(state.showInFlight).toBe(true)
    expect(state.currentDisplay).toEqual(followed)
    expect(state.history[0]).toEqual(followed)
    expect(effects).toEqual([
      {
        type: 'notice',
        tone: 'success',
        message: {
          key: 'notice.shownFollow',
          params: { ref: 'John 3:16-17' },
        },
      },
    ])
  })

  it('deduplicates and caps confirmed history at the snapshot window', () => {
    const history = Array.from({ length: 20 }, (_, index) => ({ ...manual, chapter: index + 4 }))
    const { state } = reduce(
      { ...createOperatorState(), history, historyCount: 30 },
      fixture('display_confirmed')
    )
    expect(state.history).toEqual([manual, ...history.slice(0, 19)])
    expect(state.historyCount).toBe(31)
    const again = reduce(state, fixture('display_confirmed')).state
    expect(again.history).toHaveLength(20)
    expect(again.historyCount).toBe(32)
  })
})

const settingCases = [
  {
    key: 'verseLimit',
    action: { action: 'set_verse_limit', verse_limit: 10 },
    stored: 'store_verse_limit',
  },
  {
    key: 'theme',
    action: { action: 'set_display_theme', theme: 'dark' },
    stored: 'store_display_theme',
  },
  { key: 'clearMode', action: { action: 'set_clear_mode', clear_mode: 'idle' } },
  { key: 'autoMode', action: { action: 'set_auto_mode', auto_mode: 'off' } },
  { key: 'followAuto', action: { action: 'set_follow_auto', follow_auto: false } },
] satisfies { key: keyof OperatorSettings; action: OptimisticSettingAction; stored?: string }[]

describe('optimistic settings', () => {
  it.each(settingCases)(
    'reverts $action.action to the latest confirmed value',
    ({ key, action, stored }) => {
      let state = reduce(createOperatorState(), {
        ...fixture('display_settings_confirmed'),
        data: { ...fixture('display_settings_confirmed').data, clear_mode: 'blank' },
      }).state
      state = reduce(state, fixture('auto_mode_confirmed')).state
      state = reduce(state, fixture('follow_auto_confirmed')).state
      const confirmed = state.confirmed
      for (const setting of settingCases) {
        const result = reduce(state, { type: 'set_setting', action: setting.action })
        state = result.state
        expect(result.effects.at(-1)).toEqual({ type: 'send', action: setting.action })
        expect(state.confirmed).toEqual(confirmed)
      }
      expect(state.settings[key]).not.toEqual(confirmed[key])
      const error = fixture('error')
      const result = reduce(state, { ...error, data: { ...error.data, action: action.action } })
      expect(result.state.settings).toEqual({ ...state.settings, [key]: confirmed[key] })
      expect(result.state.confirmed).toEqual(confirmed)
      expect(result.effects).toContainEqual({
        type: 'notice',
        tone: 'error',
        message: {
          key: 'notice.reverted',
          params: {
            prefix: {
              key: {
                verseLimit: 'notice.errVerseLimit',
                theme: 'notice.errTheme',
                clearMode: 'notice.errClearMode',
                autoMode: 'notice.errAutoMode',
                followAuto: 'notice.errFollowAuto',
              }[key],
            },
            reason: error.data.message,
          },
        },
      })
      const writes = result.effects.filter(effect => effect.type.startsWith('store_'))
      expect(writes).toEqual(stored ? [{ type: stored, value: confirmed[key] }] : [])
    }
  )

  it('keeps unchanged settings echoes quiet and does not persist confirmations', () => {
    let state = createOperatorState()
    for (const type of [
      'display_settings_confirmed',
      'auto_mode_confirmed',
      'follow_auto_confirmed',
    ] as const) {
      const message = fixture(type)
      state = reduce(state, message).state
      expect(reduce(state, message).effects).toEqual([])
    }
  })

  it.each(['constructor', '__proto__', null, { action: 'approve' }])(
    'handles an unknown echoed action %j',
    action => {
      const { state, effects } = reduce(populatedState(), {
        ...fixture('error'),
        data: { action, message: 'Rejected' },
      })
      expect(state.showInFlight).toBe(true)
      expect(effects).toContainEqual({
        type: 'notice',
        tone: 'error',
        message: {
          key: 'notice.failed',
          params: { prefix: { key: 'notice.errGeneric' }, reason: 'Rejected' },
        },
      })
    }
  )
})

describe('operator intents', () => {
  it('moves the cursor through the queue without sending anything', () => {
    const pending = [queued, { ...queued, id: 'second' }]
    let state = { ...createOperatorState(), pending }
    for (const [step, id] of [
      [1, queued.id],
      [1, 'second'],
      [1, 'second'],
      [-1, queued.id],
      [-1, queued.id],
    ] as const) {
      const result = reduce(state, { type: 'move_cursor', list: 'pending', step, planIds: [] })
      expect(result.state.cursor).toEqual({ list: 'pending', id })
      expect(result.effects).toEqual([])
      state = result.state
    }
    expect(reduce(state, { type: 'set_cursor', cursor: null }).state.cursor).toBeNull()
    const empty = createOperatorState()
    expect(
      reduce(empty, { type: 'move_cursor', list: 'pending', step: 1, planIds: [] }).state.cursor
    ).toBeNull()
  })

  it('moves the cursor through the plan by the IDs it is given', () => {
    const state = { ...createOperatorState(), cursor: { list: 'pending' as const, id: 'x' } }
    const planIds = ['a', 'b', 'c']
    // From another list, the first row; then on from there.
    const first = reduce(state, { type: 'move_cursor', list: 'plan', step: 1, planIds }).state
    expect(first.cursor).toEqual({ list: 'plan', id: 'a' })
    const second = reduce(first, { type: 'move_cursor', list: 'plan', step: 1, planIds }).state
    expect(second.cursor).toEqual({ list: 'plan', id: 'b' })
    const set = reduce(second, { type: 'set_cursor', cursor: { list: 'plan', id: 'c' } })
    expect(set.state.cursor).toEqual({ list: 'plan', id: 'c' })
    expect(set.effects).toEqual([])
  })

  it('shows direct coordinates without moving the cursor and rejects repeats', () => {
    const before = { ...populatedState(), showInFlight: false }
    const event = {
      type: 'show_direct',
      request: {
        book: queued.book,
        chapter: queued.chapter,
        verse_start: null,
        verse_end: null,
      },
    } as const
    const { state, effects } = reduce(before, event)
    expect(state.cursor).toBe(before.cursor)
    expect(state.showInFlight).toBe(true)
    expect(effects).toEqual([
      { type: 'send', action: { action: 'display_reference', ...event.request } },
    ])
    expect(reduce(state, event).effects).toEqual([])
  })

  it('approves a detection by ID once while the request is in flight', () => {
    const approved = reduce(
      { ...populatedState(), showInFlight: false },
      { type: 'approve_pending', id: queued.id }
    )
    expect(approved.state.showInFlight).toBe(true)
    expect(approved.state.cursor).toEqual({ list: 'pending', id: queued.id })
    expect(approved.effects).toEqual([
      { type: 'send', action: { action: 'approve', reference_id: queued.id } },
    ])
    expect(reduce(approved.state, { type: 'approve_pending', id: queued.id }).effects).toEqual([])
  })

  it('holds the discard guard through queue removal and a single cooldown', () => {
    const intent = { type: 'discard_pending', id: queued.id } as const
    const result = reduce(populatedState(), intent)
    expect(result.effects).toEqual([
      { type: 'clear_discard_timer' },
      { type: 'send', action: { action: 'discard_pending', reference_id: queued.id } },
    ])
    expect(reduce(result.state, intent).effects).toEqual([])
    const update = { ...fixture('pending_update'), data: { pending: [], count: 0 } }
    const removed = reduce(result.state, update)
    expect(removed.state.discardId).toBe(queued.id)
    expect(removed.state.discardCoolingDown).toBe(true)
    expect(removed.effects).toEqual([{ type: 'start_discard_cooldown' }])
    expect(reduce(removed.state, update).effects).toEqual([])
    const settled = reduce(removed.state, { type: 'discard_settled' })
    expect(settled.state.discardId).toBeNull()
    expect(settled.state.discardCoolingDown).toBe(false)
    expect(settled.effects).toEqual([{ type: 'clear_discard_timer' }])
  })

  it.each(['approve', 'display_reference', 'discard_pending'])(
    'settles a failed %s request',
    action => {
      const state = { ...populatedState(), discardId: queued.id, discardCoolingDown: true }
      const result = reduce(state, { ...fixture('error'), data: { action, message: 'Rejected' } })
      expect(result.state.showInFlight).toBe(action === 'discard_pending')
      expect(result.state.discardId).toBe(action === 'discard_pending' ? null : queued.id)
    }
  )

  it('clears guards on disconnect while keeping the cursor', () => {
    const state = {
      ...populatedState(),
      discardId: queued.id,
      discardCoolingDown: true,
      displayConnected: true,
    }
    const result = reduce(state, { type: 'disconnected' })
    // Whether a display is connected is unknown again until the next snapshot.
    expect(result.state).toEqual({
      ...state,
      showInFlight: false,
      discardId: null,
      discardCoolingDown: false,
      displayConnected: null,
    })
    expect(result.effects).toEqual([{ type: 'clear_discard_timer' }])
  })

  it.each([
    { type: 'clear_display', action: 'clear' },
    { type: 'clear_pending', action: 'clear_pending' },
  ] as const)('sends $action without claiming confirmation', ({ type, action }) => {
    const before = populatedState()
    expect(reduce(before, { type })).toEqual({
      state: before,
      effects: [{ type: 'send', action: { action } }],
    })
  })

  it('applies an idle image read over HTTP without effects', () => {
    const image = { version: '0123456789abcdef', fit: 'logo' } as const
    const result = reduce(createOperatorState(), { type: 'set_idle_image', image })
    expect(result.state.idleImage).toEqual(image)
    expect(result.effects).toEqual([])
  })
})
