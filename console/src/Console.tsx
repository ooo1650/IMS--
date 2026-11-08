/** IMS operator console: create and run businesses without shell commands. */
import { NavLink, Navigate, Route, Routes } from 'react-router-dom'
import { useAuth } from './auth'
import { Icon, type IconName } from './shared/Icon'
import { Button } from './shared/ui'
import Login from './Login'
import Audit from './pages/Audit'
import Businesses, { BusinessDetail } from './pages/Businesses'
import Jobs from './pages/Jobs'
import Overview from './pages/Overview'
import System from './pages/System'
import Users from './pages/Users'

const NAV: [string, string, IconName][] = [
  ['/', 'Overview', 'grid'],
  ['/businesses', 'Businesses', 'store'],
  ['/users', 'People', 'users'],
  ['/system', 'System settings', 'settings'],
  ['/jobs', 'Jobs', 'sync'],
  ['/audit', 'Audit log', 'shield'],
]

export default function Console() {
  const auth = useAuth()
  if (auth.loading) return <div className="splash">Loading…</div>
  if (!auth.me) return <Login />
  if (!auth.me.is_platform_admin) {
    return (
      <div className="console-login">
        <div className="console-login-card">
          <h2>Operators only</h2>
          <p className="muted">{auth.me.username} is a business account. The console is for platform admins; business staff use the IMS app</p>
          <Button onClick={() => void auth.logout()}>Sign out</Button>
        </div>
      </div>
    )
  }
  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark" aria-hidden><Icon name="receipt" size={20} /></span>
          <span className="brand-text">IMS Console<span className="brand-sub">Operator</span></span>
        </div>
        <nav>
          {NAV.map(([to, label, icon]) => (
            <NavLink key={to} to={to} end={to === '/'} className={({ isActive }) => (isActive ? 'nav-link active' : 'nav-link')}>
              <Icon name={icon} size={16} /> {label}
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-foot">
          <div className="sidebar-me">
            <span className="avatar">{(auth.me.name || auth.me.username).slice(0, 2).toUpperCase()}</span>
            <span className="sidebar-user"><strong>{auth.me.name || auth.me.username}</strong><span>Platform admin</span></span>
            <button className="icon-btn" title="Sign out" aria-label="Sign out" onClick={() => void auth.logout()}>
              <Icon name="logout" size={16} />
            </button>
          </div>
        </div>
      </aside>
      <div className="main">
        <main className="content">
          {auth.me.test_login && (
            <div className="alert alert-warn">Test sign-in is on for this server (IMS_TEST_LOGIN). Turn it off before real use</div>
          )}
          <Routes>
            <Route path="/" element={<Overview />} />
            <Route path="/businesses" element={<Businesses />} />
            <Route path="/businesses/:id/*" element={<BusinessDetail />} />
            <Route path="/users" element={<Users />} />
            <Route path="/system" element={<System />} />
            <Route path="/jobs" element={<Jobs />} />
            <Route path="/audit" element={<Audit />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </main>
      </div>
    </div>
  )
}
