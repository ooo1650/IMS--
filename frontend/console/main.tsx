import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { AuthProvider } from '../src/auth'
import '@fontsource/ibm-plex-sans/latin-400.css'
import '@fontsource/ibm-plex-sans/latin-500.css'
import '@fontsource/ibm-plex-mono/latin-400.css'
import '@fontsource/ibm-plex-mono/latin-500.css'
import '../src/index.css'
import './console.css'
import Console from './Console'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      {/* No offline sign-in cache: an operator's password hash never stays on a device. */}
      <AuthProvider offline={false}>
        <Console />
      </AuthProvider>
    </BrowserRouter>
  </StrictMode>,
)
