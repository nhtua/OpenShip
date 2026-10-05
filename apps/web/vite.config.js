// vite.config.js
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import tailwindcss from '@tailwindcss/vite'
import path from 'path'

const isE2E = process.env.E2E === '1'

export default defineConfig({
  plugins: [vue(), tailwindcss()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, 'src'),
    },
  },
  server: {
    // In E2E tests, don't proxy /api — Playwright route mocking handles it
    proxy: isE2E ? undefined : { '/api': 'http://localhost:8000' },
  },
})
