import { createRoot } from 'react-dom/client'
import { App } from './App'
import './launcher.css'

const container = document.getElementById('root')
if (!container) throw new Error('#root container not found')

// Mount once so the native bridge has a single poll loop.
createRoot(container).render(<App />)
