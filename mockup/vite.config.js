import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [vue(), tailwindcss()],
  resolve: {
    alias: {
      '@': '/src',
      '@ui': '/src/components/ui',
    },
  },
  server: {
    host: '0.0.0.0',
    port: 8080,
  },
})