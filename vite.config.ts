import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Build the React app into backend/static so the single FastAPI process can
// serve it in production. Node is used only at build time.
// Ports come from the environment so multiple git worktrees can run at once
// (see dev.sh RV_PORT_OFFSET). dev.sh exports BACKEND_PORT and passes --port to
// vite; the defaults keep a bare `npm run dev` on the historical 5183/8010.
const backendPort = process.env.BACKEND_PORT ?? '8010'
const frontendPort = Number(process.env.FRONTEND_PORT ?? 5183)

export default defineConfig({
  plugins: [react()],
  base: '/',
  server: {
    port: frontendPort,
    proxy: {
      // In dev, proxy API calls to the FastAPI backend for this worktree.
      '/api': `http://localhost:${backendPort}`,
    },
  },
  build: {
    outDir: 'backend/static',
    emptyOutDir: true,
  },
})
