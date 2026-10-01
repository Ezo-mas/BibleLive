import { createRoot } from 'react-dom/client'
import App from './App'

const container = document.getElementById('root')
if (!container) throw new Error('#root container not found')

// No StrictMode: it double-mounts in dev and would open two sockets.
createRoot(container).render(<App />)
