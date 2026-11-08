import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// The console is static files plus an /api proxy to the IMS backend, so the
// browser sees one origin (session cookie + CSRF work without CORS).
// Dev: the backend's runserver (default) or any IMS console endpoint, e.g.
//   IMS_API=https://ims.example.com:8443 npm run dev
const target = process.env.IMS_API ?? 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5174,
    proxy: { '/api': { target, changeOrigin: false, secure: false } },
  },
})
