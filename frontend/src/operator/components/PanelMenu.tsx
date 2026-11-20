import { useEffect, useId, useRef, useState } from 'react'
import type { KeyboardEvent, ReactNode } from 'react'
import { Button, MoreIcon } from '../../components'

interface PanelMenuItem {
  label: string
  icon: ReactNode
  onSelect: () => void
  disabled?: boolean
  destructive?: boolean
  checked?: boolean
  status?: string
}

interface PanelMenuProps {
  label: string
  items: PanelMenuItem[]
  placement: 'above' | 'beside'
  flash?: boolean
}

export function PanelMenu({ label, items, placement, flash }: PanelMenuProps) {
  const [open, setOpen] = useState(false)
  const root = useRef<HTMLDivElement>(null)
  const menu = useRef<HTMLDivElement>(null)
  const firstItem = useRef(0)
  const triggerId = useId()
  const menuId = useId()

  useEffect(() => {
    if (!open) return

    menu.current?.querySelectorAll('button')[firstItem.current]?.focus()

    function dismissOutside(event: Event) {
      if (event.target instanceof Node && !root.current?.contains(event.target)) setOpen(false)
    }

    document.addEventListener('pointerdown', dismissOutside)
    document.addEventListener('focusin', dismissOutside)
    return () => {
      document.removeEventListener('pointerdown', dismissOutside)
      document.removeEventListener('focusin', dismissOutside)
    }
  }, [open])

  function closeAndRestoreFocus() {
    setOpen(false)
    root.current?.querySelector('button')?.focus()
  }

  function handleMenuKey(event: KeyboardEvent<HTMLDivElement>) {
    const buttons = Array.from(event.currentTarget.querySelectorAll('button'))
    const active = buttons.findIndex(button => button === document.activeElement)
    let next: number
    switch (event.key) {
      case 'ArrowDown':
        next = (active + 1) % buttons.length
        break
      case 'ArrowUp':
        next = (active - 1 + buttons.length) % buttons.length
        break
      case 'Home':
        next = 0
        break
      case 'End':
        next = buttons.length - 1
        break
      case 'Escape':
        event.preventDefault()
        event.stopPropagation()
        closeAndRestoreFocus()
        return
      case 'Tab':
        closeAndRestoreFocus()
        return
      default:
        return
    }
    event.preventDefault()
    event.stopPropagation()
    buttons[next]?.focus()
  }

  return (
    <div className={`operator-panel-menu operator-panel-menu--${placement}`} ref={root}>
      <Button
        id={triggerId}
        type="button"
        variant="ghost"
        size="sm"
        className={flash ? 'btn-flash' : undefined}
        iconOnly
        icon={<MoreIcon />}
        aria-label={label}
        title={label}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        onClick={() => {
          firstItem.current = 0
          setOpen(!open)
        }}
        onKeyDown={event => {
          if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return
          event.preventDefault()
          firstItem.current = event.key === 'ArrowUp' ? items.length - 1 : 0
          setOpen(true)
        }}
      />
      {open && (
        <div
          id={menuId}
          className="operator-panel-menu-popover"
          role="menu"
          aria-labelledby={triggerId}
          ref={menu}
          onKeyDown={handleMenuKey}
        >
          {items.map(item => (
            <Button
              key={item.label}
              type="button"
              variant="ghost"
              size="sm"
              className={`operator-panel-menu-item${item.destructive ? ' is-destructive' : ''}`}
              icon={item.icon}
              role={item.checked === undefined ? 'menuitem' : 'menuitemcheckbox'}
              aria-checked={item.checked}
              aria-disabled={item.disabled || undefined}
              tabIndex={-1}
              onClick={() => {
                if (item.disabled) return
                closeAndRestoreFocus()
                item.onSelect()
              }}
            >
              {item.label}
              {item.status && <span className="operator-panel-menu-status">{item.status}</span>}
            </Button>
          ))}
        </div>
      )}
    </div>
  )
}
