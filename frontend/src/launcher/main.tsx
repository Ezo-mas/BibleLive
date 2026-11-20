import { createRoot } from 'react-dom/client'
import { App } from './App'
import './launcher.css'

const container = document.getElementById('root')
if (!container) throw new Error('#root container not found')

// A file or link dropped on the window would otherwise navigate the webview to it, and
// pywebview gives any page it loads the launcher's API. Refuse drops outright.
for (const type of ['dragover', 'drop'] as const) {
  window.addEventListener(type, event => event.preventDefault())
}

// Mount once so the native bridge has a single poll loop.
createRoot(container).render(<App />)
