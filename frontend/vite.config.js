import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The console is served by FastAPI from `frontend/dist` in production, and
// proxies to the same API during development, so there is exactly one origin
// in both cases and no CORS special-casing in the client.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true } }
  },
  build: { outDir: 'dist', sourcemap: false, chunkSizeWarningLimit: 900 }
})
