/// <reference types="vitest" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  base: '/',
  build: {
    // Emit straight into the FastAPI static dir so it can be served without
    // any extra build step wiring on the backend.
    outDir: '../src/doc_intelligence_hub/api/static',
    emptyOutDir: true,
    assetsDir: 'assets',
  },
  server: {
    proxy: {
      '/api': 'http://localhost:8001',
      '/health': 'http://localhost:8001',
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test-setup.ts'],
  },
})
