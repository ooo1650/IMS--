import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// The SPA and API share an origin (via this proxy in dev, nginx/Caddy in prod)
// so session cookies + CSRF work without cross-site cookie settings.
export default defineConfig({
  plugins: [react()],
  server: {
    // docs/user-manual.md is imported by the Help page.
    fs: { allow: ['..'] },
    proxy: {
      '/api': 'http://127.0.0.1:8000',
      '/django-admin': 'http://127.0.0.1:8000',
      '/static': 'http://127.0.0.1:8000',
    },
  },
  test: {
    environment: 'node',
  },
} as never)
