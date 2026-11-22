import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// The SPA and API share an origin (via this proxy in dev, nginx in prod)
// so session cookies + CSRF work without cross-site cookie settings.
export default defineConfig({
  plugins: [react()],
  // Dev only: pre-bundle the libraries that are imported lazily (QR, camera,
  // IndexedDB). Otherwise Vite discovers them on first use mid-session and
  // reloads the whole page to re-optimise.
  optimizeDeps: {
    include: ['react', 'react-dom/client', 'react-router-dom', 'idb', 'qrcode', '@zxing/browser'],
  },
  server: {
    // Dev only: compile the shell and the busiest screens when the server
    // starts, not when the first browser asks (the first POS load took ~30 s).
    warmup: {
      clientFiles: ['./src/main.tsx', './src/App.tsx', './src/components/Layout.tsx', './src/pages/Pos.tsx',
        './src/pages/Dashboard.tsx', './src/pages/Inventory.tsx', './src/pages/Products.tsx'],
    },
    // docs/user-manual.md is imported by the Help page.
    fs: { allow: ['..'] },
    proxy: {
      '/api': 'http://127.0.0.1:8000',
      '/django-admin': 'http://127.0.0.1:8000',
      '/static': 'http://127.0.0.1:8000',
      '/media': 'http://127.0.0.1:8000',
    },
  },
  build: {
    // Shop tablets run old browsers (an old Galaxy Tab's Chrome or Samsung Internet): keep the syntax readable to them.
    target: ['chrome95', 'edge95', 'firefox100', 'safari15'],
    rollupOptions: {
      output: {
        // React and the router change far less often than the app: in their own
        // file they stay cached across deploys, so an update downloads only app code.
        manualChunks: (id: string) =>
          /node_modules\/(react|react-dom|react-router|react-router-dom|scheduler)\//.test(id) ? 'vendor' : undefined,
      },
    },
  },
  test: {
    environment: 'node',
  },
} as never)
