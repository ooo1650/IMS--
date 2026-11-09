import { lazy, Suspense, useEffect } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'
import { useAuth } from './auth'
import { Layout, NAV } from './components/Layout'
import { PageSkeleton } from './components/ui'
import { PAGES, prefetchPage, prefetchWhenIdle } from './pages/routes'

// Only people who are signed out need the sign-in page, and only the second
// screen needs the customer display: neither is in the first download.
const Login = lazy(() => import('./pages/Login'))

// Start downloading the screen in the address bar now, in parallel with the
// session check, instead of after it.
prefetchPage(`/${window.location.pathname.split('/')[1] ?? ''}`)
const CustomerDisplay = lazy(() => import('./pages/CustomerDisplay'))

/** What the app looks like while the session is checked: sidebar + page outline, not a blank "Loading…". */
function AppSkeleton() {
  return (
    <div className="app-skeleton" aria-busy="true" aria-label="Loading">
      <aside className="app-skeleton-side">
        <span className="skel skel-title" />
        {Array.from({ length: 8 }, (_, i) => <span key={i} className="skel skel-text" style={{ width: `${50 + (i * 17) % 40}%` }} />)}
      </aside>
      <main className="app-skeleton-main"><PageSkeleton tiles={4} /></main>
    </div>
  )
}

export default function App() {
  const auth = useAuth()
  // Customer-facing window: no login, it only mirrors the POS in this browser.
  if (window.location.pathname === '/customer-display') return <Suspense fallback={null}><CustomerDisplay /></Suspense>
  if (auth.loading) return <AppSkeleton />
  if (!auth.me) return <Suspense fallback={<div className="login-wrap" aria-busy="true" />}><Login /></Suspense>
  return <SignedIn />
}

function SignedIn() {
  const auth = useAuth()
  // Route guards mirror backend permissions for UX only; the API is the authority.
  const allowed = NAV.filter((n) => n.show(auth))
  const paths = allowed.map((n) => n.to).join(',')
  // Once the first screen is up, fetch the till's code in idle time for people who sell
  // (the busiest screen, and the one that must open instantly).
  useEffect(() => {
    if (paths.split(',').includes('/pos')) prefetchWhenIdle(['/pos'])
  }, [paths])
  // Each page is lazy; the Suspense around it lives in Layout, so the sidebar stays put while it loads.
  return (
    <Suspense fallback={<AppSkeleton />}>
      <Routes>
        <Route element={<Layout />}>
          {allowed.map((n) => {
            const Page = PAGES[n.to]
            return <Route key={n.to} path={`${n.to}/*`} element={<Page />} />
          })}
          <Route path="*" element={<Navigate to={allowed[0]?.to ?? '/'} replace />} />
        </Route>
      </Routes>
    </Suspense>
  )
}
