import { defineConfig } from 'vite'
import type { Plugin } from 'vite'
import react from '@vitejs/plugin-react'
import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const projectDir = fileURLToPath(new URL('.', import.meta.url))

/**
 * In dev, make /display and /operator work without the .html extension so the
 * URLs match what the FastAPI backend serves in production.
 */
function prettyUrls(): Plugin {
  return {
    name: 'pretty-urls',
    configureServer(server) {
      server.middlewares.use((req, _res, next) => {
        const url = req.url ?? ''
        if (url === '/display' || url === '/operator') {
          req.url = `${url}.html`
        }
        next()
      })
    },
  }
}

export default defineConfig({
  plugins: [react(), prettyUrls()],
  // Pages are served by the backend at /display and /operator, so bundled
  // assets must be referenced via the /static/frontend/ mount.
  base: '/static/frontend/',
  build: {
    // Emit the production bundle into the backend's static directory.
    outDir: '../static/frontend',
    emptyOutDir: true,
    rollupOptions: {
      input: {
        display: resolve(projectDir, 'display.html'),
        operator: resolve(projectDir, 'operator.html'),
      },
    },
  },
  server: {
    // Proxy backend traffic to the FastAPI server during development. Pages are
    // served by the backend in production, so relative URLs (/ws, /api) are
    // same-origin there and only need forwarding in dev.
    proxy: {
      '/ws': {
        target: 'ws://localhost:8000',
        ws: true,
      },
      '/api': {
        target: 'http://localhost:8000',
      },
    },
  },
})
