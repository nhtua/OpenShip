// vite.config.js
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import tailwindcss from '@tailwindcss/vite'
import path from 'path'

const isE2E = process.env.E2E === '1'
const useContainerPolling = process.env.OPENSHIP_VITE_WATCH_POLLING === '1'

export default defineConfig({
  plugins: [vue(), tailwindcss()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, 'src'),
    },
  },
  server: {
    // Keep the browser origin aligned with CONNECTOR_BROWSER_ORIGINS; silently
    // choosing another port would make write-only setup fail origin checks.
    strictPort: true,
    // Container bind mounts may not forward host filesystem notifications.
    // Poll only in the Compose reload stack to keep native dev lightweight.
    watch: useContainerPolling ? { usePolling: true, interval: 500 } : undefined,
    // In E2E tests, don't proxy /api — Playwright route mocking handles it
    proxy: isE2E
      ? undefined
      : { '/api': process.env.VITE_API_PROXY_TARGET || 'http://localhost:8000' },
  },
})
