import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  base: './',
  plugins: [react()],
  // Monaco ships a vendored sanitizer; use the pinned patched package in the browser build.
  resolve: {
    alias: {
      './dompurify/dompurify.js': fileURLToPath(new URL('./node_modules/dompurify/dist/purify.es.mjs', import.meta.url)),
    },
  },

  server: {
    proxy: {
      '/api': {
        target: 'http://localhost:5052',
        changeOrigin: true,
      },

      '/metrics': {
        target: 'http://localhost:5052',
        changeOrigin: true,
      },

      '/plan_override': {
        target: 'http://localhost:5052',
        changeOrigin: true,
      },

      '/rate_override': {
        target: 'http://localhost:5052',
        changeOrigin: true,
      },
    },
  },
})
