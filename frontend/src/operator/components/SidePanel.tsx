import { useRef } from 'react'
import type { KeyboardEvent, ReactNode } from 'react'
import { useI18n } from '../../shared/i18nContext'
import type { MessageKey } from '../../shared/i18n'
import type { AutoMode } from '../../shared/types'

/** The right-hand column's lists: what was heard and what was shown. Plan has its own card. */
export type SideTab = 'pending' | 'history'

const TABS: { id: SideTab; labelKey: MessageKey }[] = [
  { id: 'pending', labelKey: 'queue.title' },
  { id: 'history', labelKey: 'history.title' },
]

const ACTIVE_MODE_LABELS: Record<Exclude<AutoMode, 'off'>, MessageKey> = {
  high: 'quick.autoHigh',
  medium_and_up: 'quick.autoMedium',
  all: 'quick.autoAll',
}

export interface SidePanelProps {
  tab: SideTab
  onTabChange: (tab: SideTab) => void
  /** Detections waiting; history is shown without a count, since it is a place to look back. */
  pendingCount: number
  autoMode: AutoMode
  /** The active tab's body (its list and footer). */
  children: ReactNode
}

/**
 * The right-hand column: Pending and History as tabs of one full-height panel.
 *
 * The queue is the one that needs the operator during a service, so it opens
 * first. Its tab keeps a count — and turns to the accent colour while it holds
 * detections and History is open — so a reference heard meanwhile is never missed.
 */
export function SidePanel({ tab, onTabChange, pendingCount, autoMode, children }: SidePanelProps) {
  const { t } = useI18n()
  const tabRefs = useRef<Record<SideTab, HTMLButtonElement | null>>({
    pending: null,
    history: null,
  })

  function handleKeyDown(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    const last = TABS.length - 1
    const target =
      event.key === 'ArrowRight'
        ? (index + 1) % TABS.length
        : event.key === 'ArrowLeft'
          ? (index - 1 + TABS.length) % TABS.length
          : event.key === 'Home'
            ? 0
            : event.key === 'End'
              ? last
              : null
    if (target === null) return
    event.preventDefault()
    const next = TABS[target].id
    onTabChange(next)
    tabRefs.current[next]?.focus()
  }

  return (
    <section className="operator-section operator-pending">
      <div className="operator-tabs" role="tablist" aria-label={t('side.aria')}>
        {TABS.map((entry, index) => {
          const count = entry.id === 'pending' ? pendingCount : null
          const active = entry.id === tab
          return (
            <button
              key={entry.id}
              ref={element => {
                tabRefs.current[entry.id] = element
              }}
              type="button"
              role="tab"
              id={`side-tab-${entry.id}`}
              aria-selected={active}
              aria-controls="side-panel"
              tabIndex={active ? 0 : -1}
              className={`operator-tab ${active ? 'is-active' : ''}`}
              data-alert={entry.id === 'pending' && !active && (count ?? 0) > 0}
              onClick={() => onTabChange(entry.id)}
              onKeyDown={event => handleKeyDown(event, index)}
            >
              {t(entry.labelKey)}
              {count !== null && <span className="operator-section-count">{count}</span>}
            </button>
          )
        })}
      </div>

      {autoMode !== 'off' && (
        <div className="operator-auto-status" data-testid="auto-mode-label">
          {t('quick.autoStatus', { mode: t(ACTIVE_MODE_LABELS[autoMode]) })}
        </div>
      )}

      <div
        id="side-panel"
        role="tabpanel"
        className="operator-tabpanel"
        aria-labelledby={`side-tab-${tab}`}
      >
        {children}
      </div>
    </section>
  )
}
