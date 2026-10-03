import { createRoot } from 'react-dom/client'
import type { ComponentType } from 'react'

/**
 * Bootstrap a React app into the #root element.
 * Shared entry point so operator/main.tsx and display/main.tsx stay one-liners.
 *
 * No StrictMode: it double-mounts in dev and would open two sockets.
 */
export function renderApp(App: ComponentType) {
  const container = document.getElementById('root')
  if (!container) throw new Error('#root container not found')

  createRoot(container).render(<App />)
}
