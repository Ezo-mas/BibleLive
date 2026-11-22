import { useCallback, useSyncExternalStore } from 'react'

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
