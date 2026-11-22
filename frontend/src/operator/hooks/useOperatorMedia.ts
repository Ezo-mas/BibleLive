import { useCallback, useRef, useSyncExternalStore } from 'react'
import type { PointerEvent } from 'react'

// Keep these queries in step with responsive.css.
export const STACKED_LAYOUT = '(max-width: 1023px)'
export const TOUCH_LAYOUT = '(hover: none) and (pointer: coarse)'

export function useOperatorMedia(mediaQuery: string) {
  const subscribe = useCallback(
    (onChange: () => void) => {
      const query = window.matchMedia?.(mediaQuery)
      query?.addEventListener('change', onChange)
      return () => query?.removeEventListener('change', onChange)
    },
    [mediaQuery]
  )
  return useSyncExternalStore(subscribe, () => window.matchMedia?.(mediaQuery).matches ?? false)
}

/**
 * Tracks the pointer that last pressed a row. A finger or pen double-tap can land
 * on a row that moved under it (an approval or replay reorders the list), and a
 * touchscreen laptop or an iPad with a trackpad does not match TOUCH_LAYOUT, so
 * only a mouse double-click may show a row.
 */
export function useMouseDoubleClick() {
  const pointerType = useRef<string | null>(null)
  const onPointerDown = useCallback((event: PointerEvent) => {
    pointerType.current = event.pointerType
  }, [])
  const fromMouse = useCallback(
    () => pointerType.current !== 'touch' && pointerType.current !== 'pen',
    []
  )
  return { onPointerDown, fromMouse }
}
