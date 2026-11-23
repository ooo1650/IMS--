import { Fragment, useState } from 'react'
import { Icon, initials } from '../components/Icon'
import { useAuth } from '../auth'
import { Badge, Button, ErrorBox, Field, fmtDate, Guide, LoadMore, Modal, PageHead, PageSkeleton, SkeletonRows, Tabs, useLoad, usePaged } from '../components/ui'
import { api, qs } from '../lib/api'
import type { Branch } from '../lib/types'

const ORG_ROLES = ['SuperAdmin', 'OrgAdmin', 'Viewer']
const BRANCH_ROLES = ['BranchManager', 'Cashier', 'InventoryClerk', 'Viewer']

interface Role { id?: number; branch: number | null; branch_name?: string | null; role: string }
interface User {
  id: number; username: string; first_name: string; last_name: string; email: string; email_verified: boolean
  otp_enabled: boolean; otp_required: boolean; is_active: boolean; last_login: string | null; roles: Role[]
}
interface Register { id: number; branch: number; branch_code: string; code: string; name: string; printer_driver: string; printer_config: { url?: string }; is_active: boolean }
interface Merchant {
  branch: number; branch_code: string; branch_name: string; enabled: boolean; merchant_code: string; username: string
  password_set: boolean; secret_set: boolean; missing: string[]; status: 'disabled' | 'mock' | 'http'
  updated_at: string | null; updated_by_name: string | null
}
interface FonepayServer {
  backend: 'disabled' | 'mock' | 'http'; base_url: string; base_url_set: boolean; mock_allowed: boolean
  allowed_hosts: string[]; updated_at: string | null; updated_by_name: string | null
}
interface MerchantList extends FonepayServer { results: Merchant[] }

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
      <Guide id="admin" title="Who can do what">
        <ul>
          <li><strong>Add user</strong>: give them a role in this business or at one branch. With an email address they can set their own password from the emailed code.</li>
          <li>Roles: <strong>Cashier</strong> sells; <strong>Inventory clerk</strong> handles stock; <strong>Branch manager</strong> runs a branch; <strong>Org admin</strong> manages the whole business.</li>
          <li>People are never deleted: deactivate them, so their sales history stays intact.</li>
        </ul>
      </Guide>
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
            {!shown.length && users.loading && <SkeletonRows cols={5} />}
            {!shown.length && !users.loading && <tr><td colSpan={5} className="empty">No users match</td></tr>}
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
    ['branches.manage', 'Manage branches & registers'], ['audit.view', 'View audit log'], ['staff.manage', 'Staff & pay (business-wide only)'],
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
  if (!roles) return <PageSkeleton rows={6} />
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
  const [f, setF] = useState({ username: user?.username ?? '', first_name: user?.first_name ?? '', last_name: user?.last_name ?? '', email: user?.email ?? '', password: '', is_active: user?.is_active ?? true, otp_enabled: user?.otp_enabled ?? false })
  const [roles, setRoles] = useState<Role[]>(user?.roles ?? [])
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const act = async (path: string, done: string) => {
    setError(null)
    setNotice(null)
    try {
      const r = await api<{ detail?: string }>(`users/${user!.id}/${path}/`, { method: 'POST' })
      setNotice(r.detail ?? done)
    } catch (e) {
      setError((e as Error).message)
    }
  }
  const orgRole = roles.find((r) => r.branch === null)?.role ?? ''
  const setOrgRole = (role: string) => setRoles([...roles.filter((r) => r.branch !== null), ...(role ? [{ branch: null, role }] : [])])
  const branchRole = (id: number) => roles.find((r) => r.branch === id)?.role ?? ''
  const setBranchRole = (id: number, role: string) => setRoles([...roles.filter((r) => r.branch !== id), ...(role ? [{ branch: id, role }] : [])])

  return (
    <Modal title={user ? `Edit ${user.username}` : 'New user'} onClose={onClose} wide>
      <ErrorBox error={error} />
      {notice && <div className="alert alert-good">{notice}</div>}
      <div className="form-grid">
        <Field label="Username"><input value={f.username} onChange={(e) => setF({ ...f, username: e.target.value })} /></Field>
        <Field label={user ? 'New password (leave blank to keep)' : 'Password'}><input type="password" value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} /></Field>
        <Field label="First name"><input value={f.first_name} onChange={(e) => setF({ ...f, first_name: e.target.value })} /></Field>
        <Field label="Last name"><input value={f.last_name} onChange={(e) => setF({ ...f, last_name: e.target.value })} /></Field>
        <Field label="Email" hint={user?.email ? (user.email_verified ? 'Verified' : 'Not verified yet: confirmed by the next code sent there') : 'Needed for sign-in codes and password resets'}>
          <input type="email" value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} />
        </Field>
        <label className="check"><input type="checkbox" checked={f.is_active} onChange={(e) => setF({ ...f, is_active: e.target.checked })} /> Active (unchecking revokes access immediately)</label>
        <label className="check" title="Admins and branch managers always need a code">
          <input type="checkbox" checked={f.otp_enabled || !!(user?.otp_required && !user.otp_enabled)} disabled={!!(user?.otp_required && !user.otp_enabled)}
            onChange={(e) => setF({ ...f, otp_enabled: e.target.checked })} /> Require an emailed sign-in code{user?.otp_required && !user.otp_enabled ? ' (required by role)' : ''}
        </label>
      </div>
      {user && (
        <div className="flags">
          <Button kind="ghost" disabled={!user.email} title={user.email ? '' : 'Add an email first'} onClick={() => void act('send-reset', 'Reset code sent.')}>Email a password reset code</Button>
          <Button kind="ghost" onClick={() => void act('sign-out', 'Signed out of every device.')}>Sign out of every device</Button>
        </div>
      )}
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
          setError(null)
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
  const merchants = useLoad(() => api<MerchantList>('fonepay-merchants/'), [])
  const [editBranch, setEditBranch] = useState<Branch | 'new' | null>(null)
  const [editReg, setEditReg] = useState<Register | { branch: number } | null>(null)
  const [editMerchant, setEditMerchant] = useState<Merchant | null>(null)
  const backend = merchants.data?.backend
  return (
    <>
      <div className="toolbar">
        <span className="muted">{branches.data?.length ?? 0} branches · each register keeps its own receipt series</span>
        <span className="spacer" />
        <Button kind="primary" onClick={() => setEditBranch('new')}><Icon name="plus" size={16} /> Add branch</Button>
      </div>
      {merchants.data && <FonepayServerCard server={merchants.data} onSaved={merchants.reload} />}
      <ErrorBox error={branches.error || registers.error || merchants.error} />
      {branches.data?.map((b) => {
        const regs = registers.data?.filter((r) => r.branch === b.id) ?? []
        const merchant = merchants.data?.results.find((m) => m.branch === b.id)
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
                  {merchant && <Button kind="ghost" onClick={() => setEditMerchant(merchant)}><Icon name="qr" size={16} /> FonePay</Button>}
                  <Button kind="ghost" onClick={() => setEditReg({ branch: b.id })}><Icon name="plus" size={16} /> Register</Button>
                </span>
              </div>
              <div className="store-meta">
                <span><Icon name="store" size={14} /> {b.address || 'No address'}</span>
                <span><Icon name="clock" size={14} /> {b.timezone}</span>
                {merchant && <span><Icon name="qr" size={14} /> FonePay {merchant.merchant_code ? <span className="mono">{merchant.merchant_code}</span> : 'no merchant'} <MerchantBadge m={merchant} /></span>}
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
                {!regs.length && <span className="muted small">No registers yet</span>}
              </div>
            </div>
          </section>
        )
      })}
      {editBranch && <BranchForm branch={editBranch === 'new' ? null : editBranch} onClose={() => setEditBranch(null)} onSaved={() => { setEditBranch(null); branches.reload() }} />}
      {editReg && <RegisterForm reg={editReg} onClose={() => setEditReg(null)} onSaved={() => { setEditReg(null); registers.reload() }} />}
      {editMerchant && backend && <MerchantForm m={editMerchant} backend={backend} onClose={() => setEditMerchant(null)} onSaved={() => { setEditMerchant(null); merchants.reload() }} />}
    </>
  )
}

/** FonePay QR set-up for the whole business: mode and API address, then the
 *  merchant account each branch's payments settle into. */
export function Payments() {
  const merchants = useLoad(() => api<MerchantList>('fonepay-merchants/'), [])
  const [edit, setEdit] = useState<Merchant | null>(null)
  const backend = merchants.data?.backend
  return (
    <div>
      <PageHead title="Payments" sub="FonePay QR: one merchant account per branch" />
      <Guide id="payments" title="Setting up FonePay QR">
        <ol>
          <li>Get your merchant details from your bank's FonePay onboarding: <strong>merchant code, username, password and secret key</strong>, plus the API address.</li>
          <li>Below, choose the mode. <strong>Live</strong> takes real payments (needs the API address). <strong>Demo</strong> lets you try the flow without money moving. <strong>Off</strong> hides FonePay at the tills.</li>
          <li>For each branch press <strong>Set up</strong>, enter its merchant details and tick <strong>Take FonePay QR payments at this branch</strong>.</li>
          <li>At that branch's till, a <strong>FonePay</strong> button appears next to Cash. The customer scans the QR and the bill completes when the bank confirms.</li>
        </ol>
      </Guide>
      <ErrorBox error={merchants.error} />
      {merchants.data && <FonepayServerCard server={merchants.data} onSaved={merchants.reload} />}
      {merchants.data && (
        <section className="card">
          <h2>Branches</h2>
          <table className="table">
            <thead><tr><th>Branch</th><th>Merchant code</th><th>Status</th><th>Missing</th><th /></tr></thead>
            <tbody>
              {merchants.data.results.map((m) => (
                <tr key={m.branch}>
                  <td><strong>{m.branch_code}</strong> <span className="muted">{m.branch_name}</span></td>
                  <td className="mono">{m.merchant_code || <span className="muted">—</span>}</td>
                  <td><MerchantBadge m={m} /></td>
                  <td className="muted small">{m.enabled && m.missing.length ? m.missing.join(', ') : ''}</td>
                  <td className="num"><Button onClick={() => setEdit(m)}>{m.merchant_code ? 'Edit' : 'Set up'}</Button></td>
                </tr>
              ))}
              {!merchants.data.results.length && <tr><td colSpan={5} className="empty">No branches yet: add one under Users &amp; access → Branches &amp; registers</td></tr>}
            </tbody>
          </table>
        </section>
      )}
      {edit && backend && <MerchantForm m={edit} backend={backend} onClose={() => setEdit(null)} onSaved={() => { setEdit(null); merchants.reload() }} />}
    </div>
  )
}

/** What the tills at this branch will do with FonePay right now. */
function MerchantBadge({ m }: { m: Merchant }) {
  if (m.status === 'http') return <Badge tone="good">Live</Badge>
  if (m.status === 'mock') return <Badge tone="info">Demo</Badge>
  if (m.enabled && m.missing.length) return <Badge tone="warn">Incomplete</Badge>
  return <Badge tone="neutral">Off</Badge>
}

function MerchantForm({ m, backend, onClose, onSaved }: { m: Merchant; backend: MerchantList['backend']; onClose: () => void; onSaved: () => void }) {
  const [f, setF] = useState({ enabled: m.enabled, merchant_code: m.merchant_code, username: m.username, password: '', secret: '' })
  const [clear, setClear] = useState({ password: false, secret: false })
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const secretHint = (isSet: boolean, what: string) =>
    isSet ? `A ${what} is saved. Leave blank to keep it.` : `No ${what} saved yet.`
  return (
    <Modal title={`FonePay merchant · ${m.branch_code} — ${m.branch_name}`} onClose={onClose}>
      <ErrorBox error={error} />
      <p className="muted small">QR payments at this branch settle into this merchant account. Get these details from the bank's FonePay merchant onboarding. The password and secret key are stored encrypted and are never shown again.</p>
      <label className="check"><input type="checkbox" checked={f.enabled} onChange={(e) => setF({ ...f, enabled: e.target.checked })} /> Take FonePay QR payments at this branch</label>
      <div className="form-grid">
        <Field label="Merchant code"><input className="mono" autoComplete="off" value={f.merchant_code} onChange={(e) => setF({ ...f, merchant_code: e.target.value.trim() })} /></Field>
        <Field label="API username"><input autoComplete="off" value={f.username} onChange={(e) => setF({ ...f, username: e.target.value })} /></Field>
        <Field label="API password" hint={clear.password ? 'Will be removed on save.' : secretHint(m.password_set, 'password')}>
          <input type="password" autoComplete="new-password" disabled={clear.password} placeholder={m.password_set ? '••••••••' : ''} value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} />
        </Field>
        <Field label="Secret key (for signing)" hint={clear.secret ? 'Will be removed on save.' : secretHint(m.secret_set, 'secret key')}>
          <input type="password" autoComplete="new-password" disabled={clear.secret} placeholder={m.secret_set ? '••••••••' : ''} value={f.secret} onChange={(e) => setF({ ...f, secret: e.target.value })} />
        </Field>
      </div>
      {(m.password_set || m.secret_set) && (
        <div className="flags">
          {m.password_set && <label className="check"><input type="checkbox" checked={clear.password} onChange={(e) => setClear({ ...clear, password: e.target.checked })} /> Remove saved password</label>}
          {m.secret_set && <label className="check"><input type="checkbox" checked={clear.secret} onChange={(e) => setClear({ ...clear, secret: e.target.checked })} /> Remove saved secret key</label>}
        </div>
      )}
      {backend === 'mock' && <p className="muted small">Demo mode: these credentials are saved but not used until the server runs live FonePay.</p>}
      {m.updated_at && <p className="muted small">Last changed {fmtDate(m.updated_at)}{m.updated_by_name ? ` by ${m.updated_by_name}` : ''}.</p>}
      <div className="modal-foot">
        <Button onClick={onClose}>Cancel</Button>
        <Button kind="primary" disabled={saving} onClick={async () => {
          setSaving(true)
          setError(null)
          try {
            const body: Record<string, unknown> = { enabled: f.enabled, merchant_code: f.merchant_code, username: f.username }
            if (clear.password) body.clear_password = true
            else if (f.password) body.password = f.password
            if (clear.secret) body.clear_secret = true
            else if (f.secret) body.secret = f.secret
            await api(`fonepay-merchants/${m.branch}/`, { method: 'PATCH', body })
            onSaved()
          } catch (e) {
            setError((e as Error).message)
          } finally {
            setSaving(false)
          }
        }}>Save</Button>
      </div>
    </Modal>
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
          setError(null)
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
      <Field label="Default printer" hint="USB and serial thermal printers are paired on each till: POS → Devices. That overrides this default.">
        <select value={f.printer_driver} onChange={(e) => setF({ ...f, printer_driver: e.target.value })}>
          <option value="browser">Browser print dialog</option>
          <option value="network_escpos">Network printer with an HTTP print endpoint (ESC/POS)</option>
          <option value="usb_bridge">Local print service on the till (HTTP)</option>
        </select>
      </Field>
      {f.printer_driver !== 'browser' && <Field label="Print endpoint URL" hint="Must accept raw ESC/POS bytes by HTTP POST. A plain port-9100 printer can't be reached from a browser."><input placeholder="http://192.168.1.50:8080/print" value={f.url} onChange={(e) => setF({ ...f, url: e.target.value })} /></Field>}
      <label className="check"><input type="checkbox" checked={f.is_active} onChange={(e) => setF({ ...f, is_active: e.target.checked })} /> Active</label>
      <div className="modal-foot">
        <Button onClick={onClose}>Cancel</Button>
        <Button kind="primary" onClick={async () => {
          setError(null)
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

const MODE_LABEL = { disabled: 'Off', mock: 'Demo (simulated payments)', http: 'Live' } as const

/** FonePay on/off/demo and the API address for the whole organization, stored
 *  in the database. Merchant credentials are set per branch below. */
function FonepayServerCard({ server, onSaved }: { server: FonepayServer; onSaved: () => void }) {
  const [mode, setMode] = useState(server.backend)
  const [baseUrl, setBaseUrl] = useState(server.base_url)
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const dirty = mode !== server.backend || baseUrl !== server.base_url
  return (
    <section className="card fonepay-server">
      <div className="card-head">
        <div>
          <h2>FonePay</h2>
          <p className="card-sub">
            {server.backend === 'http' ? 'Live: QR payments go to each branch\'s merchant account below'
              : server.backend === 'mock' ? 'Demo: payments are simulated and merchant details are not used'
              : 'Off: tills don\'t offer FonePay. Merchant details can be saved now'}
          </p>
        </div>
      </div>
      <ErrorBox error={error} />
      <div className="form-grid">
        <Field label="Mode">
          <select value={mode} onChange={(e) => setMode(e.target.value as FonepayServer['backend'])}>
            <option value="disabled">{MODE_LABEL.disabled}</option>
            <option value="http">{MODE_LABEL.http}</option>
            {(server.mock_allowed || server.backend === 'mock') && <option value="mock" disabled={!server.mock_allowed}>{MODE_LABEL.mock}</option>}
          </select>
        </Field>
        <Field label="API address" hint={`https on ${server.allowed_hosts.join(', ')}. From the bank's FonePay merchant onboarding`}>
          <input className="mono" placeholder="https://merchantapi.fonepay.com" value={baseUrl} onChange={(e) => setBaseUrl(e.target.value.trim())} />
        </Field>
      </div>
      <div className="flags">
        <Button kind="primary" disabled={!dirty || saving} onClick={async () => {
          setSaving(true)
          setError(null)
          try {
            await api('fonepay-settings', { method: 'PATCH', body: { mode, base_url: baseUrl } })
            onSaved()
          } catch (e) {
            setError((e as Error).message)
          } finally {
            setSaving(false)
          }
        }}>Save</Button>
        {server.updated_at && <span className="muted small">Last changed {fmtDate(server.updated_at)}{server.updated_by_name ? ` by ${server.updated_by_name}` : ''}</span>}
      </div>
    </section>
  )
}
