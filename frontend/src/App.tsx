import { lazy, Suspense } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'
import { useAuth } from './auth'
import { Layout, NAV } from './components/Layout'
import Login from './pages/Login'

const Pos = lazy(() => import('./pages/Pos'))
const Dashboard = lazy(() => import('./pages/Dashboard'))
const BranchReport = lazy(() => import('./pages/BranchReport'))
const Sales = lazy(() => import('./pages/Sales'))
const Products = lazy(() => import('./pages/Products'))
const Inventory = lazy(() => import('./pages/Inventory'))
const Transfers = lazy(() => import('./pages/Transfers'))
const Stocktakes = lazy(() => import('./pages/Stocktakes'))
const Purchasing = lazy(() => import('./pages/Purchasing'))
const Import = lazy(() => import('./pages/Import'))
const Admin = lazy(() => import('./pages/Admin'))
const Payments = lazy(() => import('./pages/Payments'))
const Compliance = lazy(() => import('./pages/Compliance'))
const Help = lazy(() => import('./pages/Help'))
const Account = lazy(() => import('./pages/Account'))
const CustomerDisplay = lazy(() => import('./pages/CustomerDisplay'))

const PAGES: Record<string, React.ComponentType> = {
  '/pos': Pos, '/dashboard': Dashboard, '/reports': BranchReport, '/sales': Sales, '/products': Products,
  '/inventory': Inventory, '/transfers': Transfers, '/stocktakes': Stocktakes, '/purchasing': Purchasing,
  '/import': Import, '/admin': Admin, '/payments': Payments, '/compliance': Compliance, '/help': Help, '/account': Account,
}

export default function App() {
  const auth = useAuth()
  // Customer-facing window: no login, it only mirrors the POS in this browser.
  if (window.location.pathname === '/customer-display') return <Suspense fallback={null}><CustomerDisplay /></Suspense>
  if (auth.loading) return <div className="splash">Loading…</div>
  if (!auth.me) return <Login />
  // Route guards mirror backend permissions for UX only; the API is the authority.
  const allowed = NAV.filter((n) => n.show(auth))
  return (
    <Suspense fallback={<div className="splash">Loading…</div>}>
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
