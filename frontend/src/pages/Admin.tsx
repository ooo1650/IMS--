import { Fragment, useState } from 'react'
import { Icon, initials } from '../components/Icon'
import { useAuth } from '../auth'
import { Badge, Button, ErrorBox, Field, LoadMore, Modal, PageHead, Tabs, fmtDate, useLoad, usePaged } from '../components/ui'
import { api, qs } from '../lib/api'
import type { Branch } from '../lib/types'

const ORG_ROLES = ['SuperAdmin', 'OrgAdmin', 'Viewer']
const BRANCH_ROLES = ['BranchManager', 'Cashier', 'InventoryClerk', 'Viewer']

interface Role { id?: number; branch: number | null; branch_name?: string | null; role: string }
interface User { id: number; username: string; first_name: string; last_name: string; email: string; is_active: boolean; last_login: string | null; roles: Role[] }
interface Register { id: number; branch: number; branch_code: string; code: string; name: string; printer_driver: string; printer_config: { url?: string }; is_active: boolean }

export default function Admin() {
  const { canOrg, can } = useAuth()
  const tabs: ['users' | 'roles' | 'branches' | 'audit', string][] = []
  if (canOrg('users.manage')) tabs.push(['users', 'Users'], ['roles', 'Roles & permissions'])
  if (canOrg('branches.manage')) tabs.push(['branches', 'Branches & registers'])
  if (canOrg('audit.view') || can('audit.view')) tabs.push(['audit', 'Audit log'])
  const [tab, setTab] = useState(tabs[0]?.[0] ?? 'audit')
  return (
    <div>
      <PageHead title="Users & access" sub="Role-based access control: who can do what, at which branch" />
      <Tabs value={tab} onChange={setTab} tabs={tabs} />
      {tab === 'users' && <Users />}
      {tab === 'roles' && <RolesMatrix />}
      {tab === 'branches' && <Branches />}
      {tab === 'audit' && <Audit />}
    </div>
  )
}

const ROLE_LABEL = (r: string) => r.replace(/([a-z])([A-Z])/g, '$1 $2')

function RoleChip({ role, where }: { role: string; where?: string | null }) {
  return <span className={`role-chip role-${role}`}>{ROLE_LABEL(role)}{where !== undefined && <span className="muted">@ {where ?? 'Org'}</span>}</span>
}

function Users() {
  const users = useLoad(() => api<User[]>('users/'), [])
  const branches = useLoad(() => api<Branch[]>('branches/'), [])
  const [editing, setEditing] = useState<User | 'new' | null>(null)
  const [q, setQ] = useState('')
  const [roleFilter, setRoleFilter] = useState('')
  const all = users.data ?? []
  const counts = (role: string) => all.filter((u) => u.roles.some((r) => r.role === role)).length
  const needle = q.trim().toLowerCase()
  const shown = all.filter((u) =>
    (!roleFilter || u.roles.some((r) => r.role === roleFilter)) &&
    (!needle || `${u.username} ${u.first_name} ${u.last_name} ${u.email}`.toLowerCase().includes(needle)))
  return (
    <>
      <div className="toolbar">
        <span className="input-icon search">
          <Icon name="search" size={16} />
          <input placeholder="Search users" value={q} onChange={(e) => setQ(e.target.value)} />
        </span>
        <div className="chips">
          <button className={`chip ${roleFilter ? '' : 'active'}`} onClick={() => setRoleFilter('')}>All ({all.length})</button>
          {[...ORG_ROLES, ...BRANCH_ROLES.filter((r) => r !== 'Viewer')].map((r) => (
            <button key={r} className={`chip ${roleFilter === r ? 'active' : ''}`} onClick={() => setRoleFilter(r)}>{ROLE_LABEL(r)} ({counts(r)})</button>
          ))}
        </div>
        <span className="spacer" />
        <Button kind="primary" onClick={() => setEditing('new')}><Icon name="plus" size={16} /> Add user</Button>
      </div>
      <ErrorBox error={users.error} />
      <div className="card flush">
        <table className="table">
          <thead><tr><th>Account</th><th>Roles</th><th>Last login</th><th>Status</th><th /></tr></thead>
          <tbody>
            {shown.map((u) => {
              const name = `${u.first_name} ${u.last_name}`.trim()
              return (
                <tr key={u.id}>
                  <td>
                    <div className="cell-user">
                      <span className="avatar avatar-sm">{initials(name || u.username)}</span>
                      <div>
                        <div className="cell-strong">{name || u.username}</div>
                        <div className="muted small">@{u.username}{u.email && ` · ${u.email}`}</div>
                      </div>
                    </div>
                  </td>
                  <td className="flags">
                    {u.roles.map((r) => <RoleChip key={`${r.branch}`} role={r.role} where={r.branch_name ?? null} />)}
                    {!u.roles.length && <span className="muted small">No access</span>}
                  </td>
                  <td>{fmtDate(u.last_login)}</td>
                  <td>{u.is_active ? <Badge tone="good">Active</Badge> : <Badge tone="bad">Deactivated</Badge>}</td>
                  <td className="row-actions"><Button kind="ghost" onClick={() => setEditing(u)}>Edit</Button></td>
                </tr>
              )
            })}
            {!shown.length && !users.loading && <tr><td colSpan={5} className="empty">No users match.</td></tr>}
          </tbody>
        </table>
      </div>
      {editing && branches.data && (
        <UserForm user={editing === 'new' ? null : editing} branches={branches.data} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); users.reload() }} />
      )}
    </>
  )
}

/** Human labels for permission codes (grants come from GET auth/roles). */
const PERMISSION_GROUPS: [string, [string, string][]][] = [
  ['Point of sale', [
    ['pos.sell', 'Ring up sales, take payments'], ['pos.void', 'Void a paid sale'], ['shift.manage', 'Open / close cashier shift'],
    ['returns.create', 'Start a return / credit note'], ['returns.approve', 'Approve returns over threshold'], ['discount.approve', 'Approve manager-only discounts'],
  ]],
  ['Inventory', [
    ['inventory.view', 'View stock'], ['inventory.adjust', 'Stock in/out adjustments'], ['transfer.manage', 'Branch transfers'],
    ['stocktake.manage', 'Stocktakes'], ['purchasing.manage', 'Purchase orders & receiving'], ['conflicts.resolve', 'Resolve oversold sales'],
  ]],
  ['Catalog', [['catalog.view', 'View products'], ['catalog.manage', 'Create / edit products & prices'], ['import.run', 'Bulk import']]],
  ['Reporting & admin', [
    ['reports.view', 'Branch reports'], ['dashboard.view', 'Org-wide dashboard'], ['users.manage', 'Manage users & roles'],
    ['branches.manage', 'Manage branches & registers'], ['audit.view', 'View audit log'],
  ]],
]
const ROLE_BLURB: Record<string, string> = {
  SuperAdmin: 'Full control of every branch and setting.',
  OrgAdmin: 'Runs the organization: users, branches, catalog.',
  BranchManager: 'Runs one branch: approvals, stock, purchasing.',
  Cashier: 'Sells at the till and handles returns.',
  InventoryClerk: 'Receives, counts and moves stock.',
  Viewer: 'Read-only reports and stock.',
}
const SCOPE_LABEL = { org: 'Org-wide', branch: 'Per branch', any: 'Org or branch' }
interface RoleDef { role: string; label: string; scope: keyof typeof SCOPE_LABEL; permissions: string[] }

function RolesMatrix() {
  const { data: roles, error } = useLoad(() => api<RoleDef[]>('auth/roles'), [])
  if (error) return <ErrorBox error={error} />
  if (!roles) return <div className="splash">Loading roles…</div>
  // Labels are UI copy; grants come from the server. Unknown permissions still show up.
  const known = new Set(PERMISSION_GROUPS.flatMap(([, ps]) => ps.map(([k]) => k)))
  const extra = [...new Set(roles.flatMap((r) => r.permissions))].filter((k) => !known.has(k)).map((k): [string, string] => [k, k])
  const groups = extra.length ? [...PERMISSION_GROUPS, ['Other', extra] as [string, [string, string][]]] : PERMISSION_GROUPS
  const total = known.size + extra.length
  return (
    <>
      <div className="role-cards">
        {roles.map((r) => (
          <div key={r.role} className="role-card">
            <div><RoleChip role={r.role} /></div>
            <strong>{r.permissions.length} of {total} permissions</strong>
            <span className="small">{SCOPE_LABEL[r.scope]}{ROLE_BLURB[r.role] ? ` · ${ROLE_BLURB[r.role]}` : ''}</span>
          </div>
        ))}
      </div>
      <div className="card flush">
        <div className="report-head">
          <h2>Permission matrix</h2>
          <p className="card-sub">Deny by default. A branch role applies only at its branch; org roles apply everywhere. Roles are defined on the server.</p>
        </div>
        <div className="table-scroll">
          <table className="table perm-matrix">
            <thead><tr><th>Permission</th>{roles.map((r) => <th key={r.role}>{r.label}</th>)}</tr></thead>
            <tbody>
              {groups.map(([group, perms]) => (
                <Fragment key={group}>
                  <tr className="perm-group"><td colSpan={roles.length + 1}>{group}</td></tr>
                  {perms.map(([k, label]) => (
                    <tr key={k}>
                      <td><div>{label}</div><div className="mono muted small">{k}</div></td>
                      {roles.map((r) => (
                        <td key={r.role}>
                          {r.permissions.includes(k)
                            ? <span className="perm-yes" aria-label="granted"><Icon name="check" size={18} /></span>
                            : <span className="perm-no" aria-label="not granted">—</span>}
                        </td>
                      ))}
                    </tr>
                  ))}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </>
  )
}

function UserForm({ user, branches, onClose, onSaved }: { user: User | null; branches: Branch[]; onClose: () => void; onSaved: () => void }) {
  const [f, setF] = useState({ username: user?.username ?? '', first_name: user?.first_name ?? '', last_name: user?.last_name ?? '', email: user?.email ?? '', password: '', is_active: user?.is_active ?? true })
  const [roles, setRoles] = useState<Role[]>(user?.roles ?? [])
  const [error, setError] = useState<string | null>(null)
  const orgRole = roles.find((r) => r.branch === null)?.role ?? ''
  const setOrgRole = (role: string) => setRoles([...roles.filter((r) => r.branch !== null), ...(role ? [{ branch: null, role }] : [])])
  const branchRole = (id: number) => roles.find((r) => r.branch === id)?.role ?? ''
  const setBranchRole = (id: number, role: string) => setRoles([...roles.filter((r) => r.branch !== id), ...(role ? [{ branch: id, role }] : [])])

  return (
    <Modal title={user ? `Edit ${user.username}` : 'New user'} onClose={onClose} wide>
      <ErrorBox error={error} />
      <div className="form-grid">
        <Field label="Username"><input value={f.username} onChange={(e) => setF({ ...f, username: e.target.value })} /></Field>
        <Field label={user ? 'New password (leave blank to keep)' : 'Password'}><input type="password" value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} /></Field>
        <Field label="First name"><input value={f.first_name} onChange={(e) => setF({ ...f, first_name: e.target.value })} /></Field>
        <Field label="Last name"><input value={f.last_name} onChange={(e) => setF({ ...f, last_name: e.target.value })} /></Field>
        <Field label="Email"><input value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} /></Field>
        <label className="check"><input type="checkbox" checked={f.is_active} onChange={(e) => setF({ ...f, is_active: e.target.checked })} /> Active (unchecking revokes access immediately)</label>
      </div>
      <h3>Roles</h3>
      <p className="muted small">A role applies only at its branch. Org roles apply everywhere.</p>
      <Field label="Org-wide role">
        <select value={orgRole} onChange={(e) => setOrgRole(e.target.value)}>
          <option value="">None</option>
          {ORG_ROLES.map((r) => <option key={r} value={r}>{ROLE_LABEL(r)}</option>)}
        </select>
      </Field>
      <div className="role-editor">
        {branches.map((b) => (
          <label key={b.id} className={`role-editor-row ${branchRole(b.id) ? 'on' : ''}`}>
            <span className="cell-strong"><Icon name="store" size={14} /> {b.code} — {b.name}</span>
            <select value={branchRole(b.id)} onChange={(e) => setBranchRole(b.id, e.target.value)}>
              <option value="">No access</option>
              {BRANCH_ROLES.map((r) => <option key={r} value={r}>{ROLE_LABEL(r)}</option>)}
            </select>
          </label>
        ))}
      </div>
      <div className="modal-foot">
        <Button onClick={onClose}>Cancel</Button>
        <Button kind="primary" onClick={async () => {
          try {
            const body: Record<string, unknown> = { ...f, roles: roles.map((r) => ({ branch: r.branch, role: r.role })) }
            if (!f.password) delete body.password
            await api(user ? `users/${user.id}/` : 'users/', { method: user ? 'PATCH' : 'POST', body })
            onSaved()
          } catch (e) {
            setError((e as Error).message)
          }
        }}>Save</Button>
      </div>
    </Modal>
  )
}

function Branches() {
  const branches = useLoad(() => api<Branch[]>('branches/'), [])
  const registers = useLoad(() => api<Register[]>('registers/'), [])
  const [editBranch, setEditBranch] = useState<Branch | 'new' | null>(null)
  const [editReg, setEditReg] = useState<Register | { branch: number } | null>(null)
  return (
    <>
      <div className="toolbar">
        <span className="muted">{branches.data?.length ?? 0} branches · each register keeps its own receipt series</span>
        <span className="spacer" />
        <Button kind="primary" onClick={() => setEditBranch('new')}><Icon name="plus" size={16} /> Add branch</Button>
      </div>
      <ErrorBox error={branches.error || registers.error} />
      {branches.data?.map((b) => {
        const regs = registers.data?.filter((r) => r.branch === b.id) ?? []
        return (
          <section key={b.id} className="store-card">
            <div className="store-badge">
              <Icon name="store" size={26} />
              <strong>{b.code}</strong>
              {!b.is_active && <Badge tone="bad">inactive</Badge>}
            </div>
            <div className="store-body">
              <div className="card-head">
                <h2>{b.name}</h2>
                <span className="flags">
                  <Button onClick={() => setEditBranch(b)}>Edit</Button>
                  <Button kind="ghost" onClick={() => setEditReg({ branch: b.id })}><Icon name="plus" size={16} /> Register</Button>
                </span>
              </div>
              <div className="store-meta">
                <span><Icon name="store" size={14} /> {b.address || 'No address'}</span>
                <span><Icon name="clock" size={14} /> {b.timezone}</span>
              </div>
              <div className="register-list">
                {regs.map((r) => (
                  <button key={r.id} className="register-chip" onClick={() => setEditReg(r)} title="Edit register">
                    <Icon name="printer" size={15} />
                    <span><strong>{r.name}</strong> <span className="mono muted">{r.code}</span></span>
                    <span className="muted small">{r.printer_driver.replace(/_/g, ' ')}</span>
                    {!r.is_active && <Badge tone="bad">inactive</Badge>}
                  </button>
                ))}
                {!regs.length && <span className="muted small">No registers yet.</span>}
              </div>
            </div>
          </section>
        )
      })}
      {editBranch && <BranchForm branch={editBranch === 'new' ? null : editBranch} onClose={() => setEditBranch(null)} onSaved={() => { setEditBranch(null); branches.reload() }} />}
      {editReg && <RegisterForm reg={editReg} onClose={() => setEditReg(null)} onSaved={() => { setEditReg(null); registers.reload() }} />}
    </>
  )
}

function BranchForm({ branch, onClose, onSaved }: { branch: Branch | null; onClose: () => void; onSaved: () => void }) {
  const [f, setF] = useState({ name: branch?.name ?? '', code: branch?.code ?? '', timezone: branch?.timezone ?? 'Asia/Kathmandu', address: branch?.address ?? '', is_active: branch?.is_active ?? true, org: branch?.org ?? 1 })
  const [error, setError] = useState<string | null>(null)
  return (
    <Modal title={branch ? `Edit ${branch.code}` : 'New branch'} onClose={onClose}>
      <ErrorBox error={error} />
      <Field label="Name"><input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
      <Field label="Code" hint="Prefix on receipt numbers"><input value={f.code} onChange={(e) => setF({ ...f, code: e.target.value.toUpperCase() })} /></Field>
      <Field label="Timezone (IANA)"><input value={f.timezone} onChange={(e) => setF({ ...f, timezone: e.target.value })} /></Field>
      <Field label="Address"><input value={f.address} onChange={(e) => setF({ ...f, address: e.target.value })} /></Field>
      <label className="check"><input type="checkbox" checked={f.is_active} onChange={(e) => setF({ ...f, is_active: e.target.checked })} /> Active</label>
      <div className="modal-foot">
        <Button onClick={onClose}>Cancel</Button>
        <Button kind="primary" onClick={async () => {
          try {
            await api(branch ? `branches/${branch.id}/` : 'branches/', { method: branch ? 'PATCH' : 'POST', body: f })
            onSaved()
          } catch (e) {
            setError((e as Error).message)
          }
        }}>Save</Button>
      </div>
    </Modal>
  )
}

function RegisterForm({ reg, onClose, onSaved }: { reg: Register | { branch: number }; onClose: () => void; onSaved: () => void }) {
  const existing = 'id' in reg ? reg : null
  const [f, setF] = useState({ code: existing?.code ?? '', name: existing?.name ?? '', printer_driver: existing?.printer_driver ?? 'browser', url: existing?.printer_config?.url ?? '', is_active: existing?.is_active ?? true })
  const [error, setError] = useState<string | null>(null)
  return (
    <Modal title={existing ? `Edit register ${existing.code}` : 'New register'} onClose={onClose}>
      <ErrorBox error={error} />
      <Field label="Code" hint="Each register has its own gapless receipt series"><input value={f.code} onChange={(e) => setF({ ...f, code: e.target.value.toUpperCase() })} /></Field>
      <Field label="Name"><input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
      <Field label="Printer driver">
        <select value={f.printer_driver} onChange={(e) => setF({ ...f, printer_driver: e.target.value })}>
          <option value="browser">Browser print dialog</option>
          <option value="network_escpos">Network ESC/POS</option>
          <option value="usb_bridge">USB thermal via local bridge</option>
        </select>
      </Field>
      {f.printer_driver !== 'browser' && <Field label="Printer / bridge URL"><input placeholder="http://192.168.1.50:9100/print" value={f.url} onChange={(e) => setF({ ...f, url: e.target.value })} /></Field>}
      <label className="check"><input type="checkbox" checked={f.is_active} onChange={(e) => setF({ ...f, is_active: e.target.checked })} /> Active</label>
      <div className="modal-foot">
        <Button onClick={onClose}>Cancel</Button>
        <Button kind="primary" onClick={async () => {
          try {
            const body = { branch: reg.branch, code: f.code, name: f.name, printer_driver: f.printer_driver, printer_config: f.url ? { url: f.url } : {}, is_active: f.is_active }
            await api(existing ? `registers/${existing.id}/` : 'registers/', { method: existing ? 'PATCH' : 'POST', body })
            onSaved()
          } catch (e) {
            setError((e as Error).message)
          }
        }}>Save</Button>
      </div>
    </Modal>
  )
}

interface Audit { id: number; at: string; user_name: string | null; branch_code: string | null; action: string; object_type: string; object_id: string; data: Record<string, unknown>; ip: string | null }

function Audit() {
  const [action, setAction] = useState('')
  const [user, setUser] = useState('')
  const { items, loading, error, more } = usePaged<Audit>(`audit-log/${qs({ action, user })}`)
  return (
    <>
      <div className="toolbar">
        <input className="search" placeholder="Action prefix (e.g. sale.)" value={action} onChange={(e) => setAction(e.target.value)} />
        <input className="search" placeholder="User" value={user} onChange={(e) => setUser(e.target.value)} />
      </div>
      <ErrorBox error={error} />
      <div className="card flush">
        <table className="table">
          <thead><tr><th>When</th><th>User</th><th>Branch</th><th>Action</th><th>Object</th><th>Details</th></tr></thead>
          <tbody>
            {items.map((a) => (
              <tr key={a.id}>
                <td>{fmtDate(a.at)}</td>
                <td>{a.user_name ?? <span className="muted">—</span>}</td>
                <td>{a.branch_code}</td>
                <td className="mono small">{a.action}</td>
                <td className="small">{a.object_type} {a.object_id}</td>
                <td className="mono small details">{Object.keys(a.data).length ? JSON.stringify(a.data) : ''}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <LoadMore more={more} loading={loading} />
      </div>
    </>
  )
}
