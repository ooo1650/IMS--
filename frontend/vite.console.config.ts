import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// The operator console: a separate app built from console/ into dist-console/,
// served by nginx on its own port (or subdomain). It reuses the IMS components
// and design tokens from src/ and talks to the same backend (/api/v1/platform).
export default defineConfig({
  root: 'console',
  publicDir: false, // the IMS service worker and manifest are not for the console
  plugins: [react()],
  build: { outDir: '../dist-console', emptyOutDir: true },
  server: {
    port: 5174,
    fs: { allow: ['..'] },
    proxy: { '/api': 'http://127.0.0.1:8000' },
  },
})
