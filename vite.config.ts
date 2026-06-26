import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Build the React app into backend/static so the single FastAPI process can
// serve it in production. Node is used only at build time.
export default defineConfig({
  plugins: [react()],
  base: '/',
  server: {
    // Ports offset by +10 from defaults to avoid clashing with other local
    // projects. Keep in sync with dev.sh (BACKEND_PORT / FRONTEND_PORT).
    port: 5183,
    proxy: {
      // In dev, proxy API calls to the FastAPI backend on :8010.
      '/api': 'http://localhost:8010',
    },
  },
  build: {
    outDir: 'backend/static',
    emptyOutDir: true,
  },
})
