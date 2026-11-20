import { fileURLToPath } from 'node:url'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react()],
  base: './',
  build: {
    outDir: '../static/launcher',
    emptyOutDir: true,
    rollupOptions: {
      input: fileURLToPath(new URL('./launcher.html', import.meta.url)),
    },
  },
})
