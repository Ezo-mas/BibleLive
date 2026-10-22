import { useRef } from 'react'
import type { KeyboardEvent, ReactNode } from 'react'
import { useI18n } from '../../shared/i18nContext'
import type { MessageKey } from '../../shared/i18n'

/** The sidebar's lists: what was heard, what is planned, what was shown. */
export type SideTab = 'pending' | 'plan' | 'history'

const TABS: { id: SideTab; labelKey: MessageKey }[] = [
  { id: 'pending', labelKey: 'queue.title' },
  { id: 'plan', labelKey: 'plan.title' },
  { id: 'history', labelKey: 'history.title' },
]

export interface SidePanelProps {
  tab: SideTab
  onTabChange: (tab: SideTab) => void
  /** Items per tab; history is shown without one, since it is a place to look back. */
  counts: { pending: number; plan: number }
  /** The active tab's body (its list and footer). */
  children: ReactNode
}

/**
 * The right-hand column: Pending, Plan and History as tabs of one panel.
 *
 * Only one list needs the operator at a time, and the queue is the one that does
 * during a service, so it opens first. Its tab keeps a count — and turns to the
 * accent colour while it holds detections and is out of view — so a reference
 * heard while the Plan is open is never missed.
 */
export function SidePanel({ tab, onTabChange, counts, children }: SidePanelProps) {
  const { t } = useI18n()
  const tabRefs = useRef<Record<SideTab, HTMLButtonElement | null>>({
    pending: null,
    plan: null,
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
          const count = entry.id === 'history' ? null : counts[entry.id]
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
