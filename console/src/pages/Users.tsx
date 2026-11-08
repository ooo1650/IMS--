/** People: every account, with roles in any number of businesses (the owner
 *  usually has one in each). Operators (platform admins) hold no business roles. */
import { useEffect, useRef, useState } from 'react'
import { Badge, Button, ErrorBox, Field, Modal, PageHead, fmtDate, useLoad } from '../shared/ui'
import { api, qs } from '../shared/api'
import type { Business } from './Businesses'

interface Role { org: number; org_name?: string; branch: number | null; branch_code?: string | null; role: string }
export interface Person {
  id: number; username: string; first_name: string; last_name: string; email: string; email_verified: boolean
  is_active: boolean; is_platform_admin: boolean; last_login: string | null; roles: Role[]
}
interface BranchLite { id: number; code: string; name: string }

const BUSINESS_ROLES = ['SuperAdmin', 'OrgAdmin', 'Viewer']
const BRANCH_ROLES = ['BranchManager', 'Cashier', 'InventoryClerk', 'Viewer']
const label = (r: string) => r.replace(/([a-z])([A-Z])/g, '$1 $2')

export default function Users() {
  const [q, setQ] = useState('')
  const people = useLoad(() => api<Person[]>(`platform/users${qs({ q })}`), [q])
  const [editing, setEditing] = useState<Person | 'new' | null>(null)
  return (
    <div>
      <PageHead title="People" sub="Every account, across all businesses">
        <input className="search" placeholder="Search name, username or email" value={q} onChange={(e) => setQ(e.target.value)} />
        <Button kind="primary" onClick={() => setEditing('new')}>Add person</Button>
      </PageHead>
      <ErrorBox error={people.error} />
      <PeopleTable people={people.data ?? []} onEdit={setEditing} />
      {editing && <PersonForm person={editing === 'new' ? null : editing} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); people.reload() }} />}
    </div>
  )
}

/** People with a role in one business (the business page's People tab). */
export function PeopleOfBusiness({ orgId, orgName }: { orgId: number; orgName: string }) {
  const people = useLoad(() => api<Person[]>(`platform/users${qs({ business: String(orgId) })}`), [orgId])
  const [editing, setEditing] = useState<Person | 'new' | null>(null)
  return (
    <section className="card flush">
      <div className="report-head card-head">
        <div><h2>People</h2><p className="card-sub">Roles here apply only to {orgName}</p></div>
        <Button kind="primary" onClick={() => setEditing('new')}>Add person</Button>
      </div>
      <ErrorBox error={people.error} />
      <PeopleTable people={people.data ?? []} onEdit={setEditing} />
      {editing && <PersonForm person={editing === 'new' ? null : editing} defaultOrg={orgId}
        onClose={() => setEditing(null)} onSaved={() => { setEditing(null); people.reload() }} />}
    </section>
  )
}

function PeopleTable({ people, onEdit }: { people: Person[]; onEdit: (p: Person) => void }) {
  return (
    <div className="card flush table-scroll">
      <table className="table">
        <thead><tr><th>Person</th><th>Roles</th><th>Last sign-in</th><th>Status</th><th /></tr></thead>
        <tbody>
          {people.map((p) => (
            <tr key={p.id}>
              <td><div className="cell-strong">{`${p.first_name} ${p.last_name}`.trim() || p.username}</div>
                <div className="muted small"><span className="mono">@{p.username}</span>{p.email && ` · ${p.email}`}</div></td>
              <td className="flags">
                {p.is_platform_admin && <Badge tone="info">Operator</Badge>}
                {p.roles.map((r, i) => <span key={i} className="role-chip">{label(r.role)} <span className="muted">@ {r.branch_code ?? r.org_name}</span></span>)}
                {!p.is_platform_admin && !p.roles.length && <span className="muted small">No access</span>}
              </td>
              <td>{fmtDate(p.last_login)}</td>
              <td>{p.is_active ? <span className="status-quiet">Active</span> : <Badge tone="bad">deactivated</Badge>}</td>
              <td className="row-actions"><Button kind="ghost" onClick={() => onEdit(p)}>Edit</Button></td>
            </tr>
          ))}
          {!people.length && <tr><td colSpan={5} className="empty">Nobody yet</td></tr>}
        </tbody>
      </table>
    </div>
  )
}

function PersonForm({ person, defaultOrg, onClose, onSaved }: { person: Person | null; defaultOrg?: number; onClose: () => void; onSaved: () => void }) {
  const businesses = useLoad(() => api<Business[]>('platform/businesses'), [])
  const [branches, setBranches] = useState<Record<number, BranchLite[]>>({})
  const [f, setF] = useState({ username: person?.username ?? '', first_name: person?.first_name ?? '', last_name: person?.last_name ?? '',
    email: person?.email ?? '', password: '', is_active: person?.is_active ?? true, send_setup_code: !person })
  const [roles, setRoles] = useState<Role[]>(person?.roles.map(({ org, branch, role }) => ({ org, branch, role }))
    ?? (defaultOrg ? [{ org: defaultOrg, branch: null, role: 'OrgAdmin' }] : []))
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const requested = useRef(new Set<number>())
  const loadBranches = (org: number) => {
    if (requested.current.has(org)) return // one request per business
    requested.current.add(org)
    void api<BranchLite[]>(`platform/businesses/${org}/branches`).then((b) => setBranches((cur) => ({ ...cur, [org]: b })))
  }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => roles.forEach((r) => loadBranches(r.org)), [roles])
  const setRole = (i: number, patch: Partial<Role>) => setRoles(roles.map((r, j) => (j === i ? { ...r, ...patch } : r)))
  const act = async (action: string) => {
    setError(null)
    try {
      const r = await api<{ detail: string }>(`platform/users/${person!.id}/${action}`, { method: 'POST' })
      setNotice(r.detail)
    } catch (e) {
      setError((e as Error).message)
    }
  }

  return (
    <Modal title={person ? `Edit ${person.username}` : 'Add person'} onClose={onClose} wide>
      <ErrorBox error={error} />
      {notice && <div className="alert alert-good">{notice}</div>}
      <div className="form-grid">
        <Field label="Username"><input autoCapitalize="none" value={f.username} onChange={(e) => setF({ ...f, username: e.target.value })} /></Field>
        <Field label="Email" hint="Sign-in and setup codes go here"><input type="email" value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} /></Field>
        <Field label="First name"><input value={f.first_name} onChange={(e) => setF({ ...f, first_name: e.target.value })} /></Field>
        <Field label="Last name"><input value={f.last_name} onChange={(e) => setF({ ...f, last_name: e.target.value })} /></Field>
        <Field label={person ? 'New password (blank keeps it)' : 'Password (optional)'} hint="Or let them choose one from an emailed code">
          <input type="password" autoComplete="new-password" value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} />
        </Field>
      </div>
      <label className="check"><input type="checkbox" checked={f.send_setup_code} onChange={(e) => setF({ ...f, send_setup_code: e.target.checked })} /> Email a code to set their password</label>
      {person && <label className="check"><input type="checkbox" checked={f.is_active} onChange={(e) => setF({ ...f, is_active: e.target.checked })} /> Active (unticking signs them out everywhere)</label>}
      {person?.is_platform_admin ? <p className="muted">Operator account: no business roles</p> : (
        <>
          <h3>Roles</h3>
          <div className="role-rows">
            {roles.map((r, i) => (
              <div key={i} className="role-row">
                <select aria-label="Business" value={r.org} onChange={(e) => { const org = Number(e.target.value); loadBranches(org); setRole(i, { org, branch: null }) }}>
                  {businesses.data?.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
                </select>
                <select aria-label="Where" value={r.branch ?? ''} onChange={(e) => setRole(i, { branch: e.target.value ? Number(e.target.value) : null, role: e.target.value ? 'Cashier' : 'OrgAdmin' })}>
                  <option value="">Whole business</option>
                  {(branches[r.org] ?? []).map((b) => <option key={b.id} value={b.id}>{b.code} — {b.name}</option>)}
                </select>
                <select aria-label="Role" value={r.role} onChange={(e) => setRole(i, { role: e.target.value })}>
                  {(r.branch ? BRANCH_ROLES : BUSINESS_ROLES).map((x) => <option key={x} value={x}>{label(x)}</option>)}
                </select>
                <Button kind="ghost" onClick={() => setRoles(roles.filter((_, j) => j !== i))}>Remove</Button>
              </div>
            ))}
          </div>
          <Button kind="ghost" disabled={!businesses.data?.length}
            onClick={() => setRoles([...roles, { org: defaultOrg ?? businesses.data![0].id, branch: null, role: 'OrgAdmin' }])}>Add role</Button>
        </>
      )}
      {person && (
        <div className="flags" style={{ marginTop: 16 }}>
          <Button kind="ghost" disabled={!person.email} onClick={() => void act('send-reset')}>Email a password reset code</Button>
          <Button kind="ghost" onClick={() => void act('sign-out')}>Sign out of every device</Button>
        </div>
      )}
      <div className="modal-foot">
        <Button onClick={onClose}>Cancel</Button>
        <Button kind="primary" busy={busy} onClick={async () => {
          setBusy(true)
          setError(null)
          try {
            const body: Record<string, unknown> = { ...f }
            if (!f.password) delete body.password
            if (!person?.is_platform_admin) body.roles = roles
            await api(person ? `platform/users/${person.id}` : 'platform/users', { method: person ? 'PATCH' : 'POST', body })
            onSaved()
          } catch (e) {
            setError((e as Error).message)
          } finally {
            setBusy(false)
          }
        }}>Save</Button>
      </div>
    </Modal>
  )
}
