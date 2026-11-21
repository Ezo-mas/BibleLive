import App from './App'
import StreamOverlay from './StreamOverlay'
import StreamOverlayPreview from './StreamOverlayPreview'
import { isStreamOverlay, isStreamOverlayPreview } from './streamOverlayOptions'
import { renderApp } from '../shared/renderApp'

// `/display?mode=obs` is the stream lower third (`&preview=1`: the console's
// socket-free preview of it); the projector page otherwise.
const search = window.location.search
renderApp(
  isStreamOverlay(search)
    ? isStreamOverlayPreview(search)
      ? StreamOverlayPreview
      : StreamOverlay
    : App
)
