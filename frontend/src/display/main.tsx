import App from './App'
import StreamOverlay from './StreamOverlay'
import { isStreamOverlay } from './streamOverlayOptions'
import { renderApp } from '../shared/renderApp'

// `/display?mode=obs` is the stream lower third; the projector page otherwise.
renderApp(isStreamOverlay(window.location.search) ? StreamOverlay : App)
