import { useState } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import { useAuth } from '../auth'
import { useOnline, useQueue } from '../lib/sync'
import { ErrorBoundary } from './ErrorBoundary'
import { SyncQueue } from './SyncQueue'
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
  { to: '/payments', label: 'Payments', icon: 'qr', group: 'Admin', subtitle: 'FonePay QR merchant for each branch', show: (a) => a.canOrg('branches.manage') },
  { to: '/account', label: 'My account', icon: 'user', group: 'Admin', subtitle: 'Password, email and sign-in code', show: () => true },
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
  const [showQueue, setShowQueue] = useState(false)
  const items = NAV.filter((n) => n.show(auth))
  const roleLabel = auth.me?.org_role ?? auth.branch?.role ?? ''

  return (
    <div className="shell">
      {showQueue && pending > 0 && <SyncQueue queue={queue} onClose={() => setShowQueue(false)} />}
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark" aria-hidden><Icon name="receipt" size={20} /></span>
          <span className="brand-text">IMS<span className="brand-sub">{auth.me?.active_org_name ?? 'Inventory & POS'}</span></span>
        </div>
        {(auth.me?.businesses.length ?? 0) > 1 && (
          <label className="branch-pick">
            <Icon name="landmark" size={16} />
            <select className="branch-select" aria-label="Business" value={auth.me?.active_org_id ?? ''}
              onChange={(e) => void auth.selectBusiness(Number(e.target.value))}>
              {!auth.me?.active_org_id && <option value="">Select business</option>}
              {auth.me?.businesses.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
            </select>
          </label>
        )}
        <label className="branch-pick">
          <Icon name="store" size={16} />
          <select
            className="branch-select"
            aria-label="Active branch"
            value={auth.branch?.id ?? ''}
            onChange={(e) => void auth.selectBranch(Number(e.target.value))}
          >
            {!auth.branch && <option value="">Select branch</option>}
            {auth.me?.branches.map((b) => (
              <option key={b.id} value={b.id}>
                {b.code} — {b.name}
              </option>
            ))}
          </select>
        </label>
        {GROUPS.map((g) => {
          const links = items.filter((n) => n.group === g)
          if (!links.length) return null
          return (
            <div key={g}>
              <div className="nav-group">{g}</div>
              <nav>
                {links.map((n) => (
                  <NavLink key={n.to} to={n.to} className={({ isActive }) => (isActive ? 'nav-link active' : 'nav-link')}>
                    <Icon name={n.icon} size={16} /> {n.label}
                  </NavLink>
                ))}
              </nav>
            </div>
          )
        })}
        <div className="sidebar-foot">
          <div className="status-line" role="status">
            <span className={`status-dot ${online ? '' : 'off'}`} aria-hidden />
            {online ? 'Online' : 'Offline'}
            {pending > 0 && (
              <button type="button" className={`link-btn ${errors ? 'bad' : 'warn'}`} title="Offline sales waiting to sync" onClick={() => setShowQueue(true)}>
                · {pending} queued{errors ? `, ${errors} failed` : ''}
              </button>
            )}
            {auth.offlineSession && <span className="warn">· offline sign-in</span>}
          </div>
          <div className="sidebar-me">
            <span className="avatar">{initials(auth.me?.name || auth.me?.username)}</span>
            <NavLink to="/account" className="sidebar-user" title="My account">
              <strong>{auth.me?.name || auth.me?.username}</strong>
              <span>{roleLabel.replace(/([a-z])([A-Z])/g, '$1 $2')}</span>
            </NavLink>
            <button className="icon-btn" title="Log out" aria-label="Log out" onClick={() => void auth.logout()}>
              <Icon name="logout" size={16} />
            </button>
          </div>
        </div>
      </aside>
      <div className="main">
        <main className="content">
          {auth.me?.test_login && (
            <div className="alert alert-warn test-login-banner" role="status">
              Test sign-in is on: anyone on this network can sign in as any user. Set <code>IMS_TEST_LOGIN=false</code> before real use
            </div>
          )}
          {auth.branch || auth.me?.org_role ? <ErrorBoundary key={location.pathname}><Outlet /></ErrorBoundary>
            : auth.me?.is_platform_admin && !auth.me.businesses.length
              ? <div className="empty">This is an operator account. Businesses, branches and admin accounts are managed in the operator console</div>
              : <div className="empty">Select a branch to continue</div>}
        </main>
      </div>
    </div>
  )
}
