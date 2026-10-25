import { NavLink, Outlet, useLocation } from 'react-router-dom'
import { useAuth } from '../auth'
import { useOnline, useQueue } from '../lib/sync'
import { ErrorBoundary } from './ErrorBoundary'
import { Icon, initials, type IconName } from './Icon'

export interface NavItem {
  to: string
  label: string
  icon: IconName
  group: 'Sell' | 'Stock' | 'Insights' | 'Admin'
  subtitle: string
  show: (a: ReturnType<typeof useAuth>) => boolean
}

// Order matters: the first visible entry is the landing page (App.tsx).
export const NAV: NavItem[] = [
  { to: '/pos', label: 'Point of Sale', icon: 'cart', group: 'Sell', subtitle: 'Quick billing and transaction processing', show: (a) => a.can('pos.sell') },
  { to: '/dashboard', label: 'Dashboard', icon: 'grid', group: 'Insights', subtitle: 'Organization-wide overview', show: (a) => a.canOrg('dashboard.view') },
  { to: '/reports', label: 'Branch report', icon: 'chart', group: 'Insights', subtitle: 'Sales and stock for this branch', show: (a) => a.can('reports.view') },
  { to: '/sales', label: 'Sales', icon: 'receipt', group: 'Sell', subtitle: 'Invoices, returns and reprints', show: (a) => a.can('pos.sell') || a.can('reports.view') },
  { to: '/products', label: 'Products', icon: 'package', group: 'Stock', subtitle: 'Catalog, prices and barcodes', show: (a) => a.can('catalog.view') || a.canOrg('catalog.view') },
  { to: '/inventory', label: 'Inventory', icon: 'boxes', group: 'Stock', subtitle: 'Manage stock on hand and movements', show: (a) => a.can('inventory.view') },
  { to: '/transfers', label: 'Transfers', icon: 'transfer', group: 'Stock', subtitle: 'Move stock between branches', show: (a) => a.can('inventory.view') },
  { to: '/stocktakes', label: 'Stocktakes', icon: 'clipboard', group: 'Stock', subtitle: 'Physical counts and variances', show: (a) => a.can('stocktake.manage') },
  { to: '/purchasing', label: 'Purchasing', icon: 'truck', group: 'Stock', subtitle: 'Suppliers, purchase orders and receiving', show: (a) => a.can('purchasing.manage') || a.can('reports.view') },
  { to: '/compliance', label: 'Tax & IRD', icon: 'landmark', group: 'Insights', subtitle: 'VAT registers, CBMS and fiscal years', show: (a) => a.can('reports.view') || a.canOrg('reports.view') || a.canOrg('dashboard.view') },
  { to: '/import', label: 'Import', icon: 'upload', group: 'Admin', subtitle: 'Bulk-load products and stock', show: (a) => a.can('import.run') || a.canOrg('catalog.manage') },
  { to: '/admin', label: 'Users & access', icon: 'shield', group: 'Admin', subtitle: 'Users, roles, branches and audit log', show: (a) => a.canOrg('users.manage') || a.canOrg('branches.manage') || a.can('audit.view') },
  { to: '/help', label: 'Help', icon: 'help', group: 'Admin', subtitle: 'User manual', show: () => true },
]

const GROUPS: NavItem['group'][] = ['Sell', 'Insights', 'Stock', 'Admin']

export function Layout() {
  const auth = useAuth()
  const location = useLocation()
  const online = useOnline()
  const queue = useQueue()
  const pending = queue.length
  const errors = queue.filter((q) => q.status === 'error').length
  const items = NAV.filter((n) => n.show(auth))
  const current = items.find((n) => location.pathname.startsWith(n.to))
  const roleLabel = auth.me?.org_role ?? auth.branch?.role ?? ''

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark" aria-hidden><Icon name="layers" size={20} /></span>
          <span className="brand-text">IMS<span className="brand-sub">Inventory &amp; POS</span></span>
        </div>
        {GROUPS.map((g) => {
          const links = items.filter((n) => n.group === g)
          if (!links.length) return null
          return (
            <div key={g}>
              <div className="nav-group">{g}</div>
              <nav>
                {links.map((n) => (
                  <NavLink key={n.to} to={n.to} className={({ isActive }) => (isActive ? 'nav-link active' : 'nav-link')}>
                    <Icon name={n.icon} /> {n.label}
                  </NavLink>
                ))}
              </nav>
            </div>
          )
        })}
        <div className="sidebar-foot">
          <span className="avatar">{initials(auth.me?.name || auth.me?.username)}</span>
          <div className="sidebar-user">
            <strong>{auth.me?.name || auth.me?.username}</strong>
            <span>{roleLabel.replace(/([a-z])([A-Z])/g, '$1 $2')}</span>
          </div>
          <button className="icon-btn" title="Log out" aria-label="Log out" onClick={() => void auth.logout()}>
            <Icon name="logout" />
          </button>
        </div>
      </aside>
      <div className="main">
        <header className="topbar">
          <div className="topbar-title">
            <strong>{current?.label ?? 'IMS'}</strong>
            {current && <span>{current.subtitle}</span>}
          </div>
          <div className="topbar-right">
            {pending > 0 && (
              <span className={`pill ${errors ? 'pill-bad' : 'pill-warn'}`} title="Offline sales waiting to sync">
                <Icon name="sync" size={13} /> {pending} queued{errors ? ` · ${errors} failed` : ''}
              </span>
            )}
            <span className={`pill ${online ? 'pill-good' : 'pill-bad'}`}>
              <span className="dot" /> {online ? 'Online' : 'Offline'}
            </span>
            {auth.offlineSession && <span className="pill pill-warn">Offline login</span>}
            <label className="branch-pick">
              <Icon name="store" size={16} />
              <select
                className="branch-select"
                aria-label="Active branch"
                value={auth.branch?.id ?? ''}
                onChange={(e) => void auth.selectBranch(Number(e.target.value))}
              >
                {!auth.branch && <option value="">Select branch…</option>}
                {auth.me?.branches.map((b) => (
                  <option key={b.id} value={b.id}>
                    {b.code} — {b.name} ({b.role})
                  </option>
                ))}
              </select>
            </label>
          </div>
        </header>
        <main className="content">
          {auth.branch || auth.me?.org_role ? <ErrorBoundary key={location.pathname}><Outlet /></ErrorBoundary> : <div className="empty">Select a branch to continue.</div>}
        </main>
      </div>
    </div>
  )
}
