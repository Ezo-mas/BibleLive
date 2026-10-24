import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: './src/test/setup.ts',
    // Under --coverage (as CI runs it) the heavier App.* tests take several seconds
    // each, and more workers than this starve them on an 8-core box; 4 matches a CI
    // runner. The longer timeout still fails a hung test, just not a slow one.
    maxWorkers: 4,
    testTimeout: 15_000,
    coverage: {
      provider: 'v8',
      reporter: ['text', 'json', 'html'],
      exclude: [
        'node_modules/',
        'src/test/',
        '**/*.d.ts',
        '**/*.config.*',
        '**/dist/**',
      ],
    },
  },
})
