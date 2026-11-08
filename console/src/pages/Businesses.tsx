/** Businesses: each is separately registered (own PAN, catalog, invoices,
 *  reports). Create one with its first branch and admin, then manage it here. */
import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { Badge, Button, ErrorBox, Field, Modal, PageHead, Tabs, fmtDate, useLoad } from '../shared/ui'
import { api } from '../shared/api'
import { toPaisa, toRupeesInput } from '../shared/money'
import { PeopleOfBusiness } from './Users'

export interface Business {
  id: number; name: string; name_np: string; code: string; trade: string; pan: string; address: string; phone: string
  email: string; vat_registered: boolean; business_type: 'goods' | 'services' | 'mixed'; is_active: boolean
  branches: number; users: number; products: number
}

const save = async <T,>(fn: () => Promise<T>, setError: (e: string | null) => void, setBusy: (b: boolean) => void) => {
  setBusy(true)
  setError(null)
  try {
    return await fn()
  } catch (e) {
    setError((e as Error).message)
    return undefined
  } finally {
    setBusy(false)
  }
}

export default function Businesses() {
  const list = useLoad(() => api<Business[]>('platform/businesses'), [])
  const [creating, setCreating] = useState(false)
  return (
    <div>
      <PageHead title="Businesses" sub="Each one is separately registered: its own PAN, catalog, invoices and reports">
        <Button kind="primary" onClick={() => setCreating(true)}>New business</Button>
      </PageHead>
      <ErrorBox error={list.error} />
      <div className="card flush">
        <table className="table">
          <thead><tr><th>Business</th><th>PAN</th><th>Code</th><th className="num">Branches</th><th className="num">People</th><th className="num">Products</th><th /></tr></thead>
          <tbody>
            {list.data?.map((b) => (
              <tr key={b.id}>
                <td><Link to={`/businesses/${b.id}`} className="cell-strong">{b.name}</Link> <span className="muted small">{b.trade}</span></td>
                <td className="mono">{b.pan || '–'}</td>
                <td className="mono">{b.code}</td>
                <td className="num">{b.branches}</td>
                <td className="num">{b.users}</td>
                <td className="num">{b.products}</td>
                <td>{!b.is_active && <Badge tone="bad">inactive</Badge>}</td>
              </tr>
            ))}
            {list.data && !list.data.length && <tr><td colSpan={7} className="empty">No businesses yet</td></tr>}
          </tbody>
        </table>
      </div>
      {creating && <NewBusiness onClose={() => setCreating(false)} />}
    </div>
  )
}

function NewBusiness({ onClose }: { onClose: () => void }) {
  const nav = useNavigate()
  const [f, setF] = useState({ name: '', name_np: '', pan: '', trade: '', address: '', phone: '', email: '', business_type: 'goods',
    first_branch_name: '', first_branch_code: '', admin_name: '', admin_username: '', admin_email: '' })
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setF({ ...f, [k]: e.target.value })
  return (
    <Modal title="New business" onClose={onClose} wide>
      <ErrorBox error={error} />
      <h3>Registration</h3>
      <div className="form-grid">
        <Field label="Registered name"><input value={f.name} onChange={set('name')} required /></Field>
        <Field label="Name in Nepali" hint="Printed on its tax invoices"><input lang="ne" value={f.name_np} onChange={set('name_np')} /></Field>
        <Field label="PAN / VAT number"><input className="mono" inputMode="numeric" maxLength={9} value={f.pan} onChange={set('pan')} /></Field>
        <Field label="What it sells" hint="e.g. Footwear"><input value={f.trade} onChange={set('trade')} /></Field>
        <Field label="Supplies">
          <select value={f.business_type} onChange={set('business_type')}>
            <option value="goods">Goods</option><option value="services">Services</option><option value="mixed">Goods and services</option>
          </select>
        </Field>
        <Field label="Address"><input value={f.address} onChange={set('address')} /></Field>
        <Field label="Phone"><input value={f.phone} onChange={set('phone')} /></Field>
        <Field label="Email"><input type="email" value={f.email} onChange={set('email')} /></Field>
      </div>
      <h3>First branch (optional)</h3>
      <div className="form-grid">
        <Field label="Branch name"><input value={f.first_branch_name} onChange={set('first_branch_name')} /></Field>
        <Field label="Branch code" hint="Starts every invoice number, e.g. KTM-2083-84-000001"><input className="mono" maxLength={16} value={f.first_branch_code} onChange={(e) => setF({ ...f, first_branch_code: e.target.value.toUpperCase() })} /></Field>
      </div>
      <h3>First admin (optional)</h3>
      <p className="muted small">They get an email with a code to set their own password</p>
      <div className="form-grid">
        <Field label="Full name"><input value={f.admin_name} onChange={set('admin_name')} /></Field>
        <Field label="Username"><input autoCapitalize="none" value={f.admin_username} onChange={set('admin_username')} /></Field>
        <Field label="Email"><input type="email" value={f.admin_email} onChange={set('admin_email')} /></Field>
      </div>
      <div className="modal-foot">
        <Button onClick={onClose}>Cancel</Button>
        <Button kind="primary" busy={busy} disabled={!f.name.trim()} onClick={async () => {
          const b = await save(() => api<Business>('platform/businesses', { body: f }), setError, setBusy)
          if (b) nav(`/businesses/${b.id}`)
        }}>Create business</Button>
      </div>
    </Modal>
  )
}

type Tab = 'details' | 'branches' | 'people' | 'rules' | 'tax' | 'fonepay'

export function BusinessDetail() {
  const id = Number(useParams().id)
  const biz = useLoad(() => api<Business>(`platform/businesses/${id}`), [id])
  const [tab, setTab] = useState<Tab>('details')
  if (biz.error) return <ErrorBox error={biz.error} />
  if (!biz.data) return <div className="splash">Loading…</div>
  const b = biz.data
  return (
    <div>
      <p className="small"><Link to="/businesses">Businesses</Link></p>
      <PageHead title={b.name} sub={[b.trade, b.pan && `PAN ${b.pan}`, `code ${b.code}`].filter(Boolean).join(' · ')}>
        {!b.is_active && <Badge tone="bad">inactive</Badge>}
      </PageHead>
      <Tabs value={tab} onChange={setTab} tabs={[['details', 'Details'], ['branches', 'Branches & registers'], ['people', 'People'],
        ['rules', 'Shop rules'], ['tax', 'Tax & CBMS'], ['fonepay', 'FonePay']]} />
      {tab === 'details' && <Details b={b} onSaved={biz.reload} />}
      {tab === 'branches' && <Branches orgId={id} />}
      {tab === 'people' && <PeopleOfBusiness orgId={id} orgName={b.name} />}
      {tab === 'rules' && <Rules orgId={id} />}
      {tab === 'tax' && <Tax orgId={id} />}
      {tab === 'fonepay' && <Fonepay orgId={id} />}
    </div>
  )
}

function Details({ b, onSaved }: { b: Business; onSaved: () => void }) {
  const [f, setF] = useState(b)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const set = (k: keyof Business) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setF({ ...f, [k]: e.target.value })
  return (
    <section className="card">
      <ErrorBox error={error} />
      {notice && <div className="alert alert-good">{notice}</div>}
      <div className="form-grid">
        <Field label="Registered name"><input value={f.name} onChange={set('name')} /></Field>
        <Field label="Name in Nepali"><input lang="ne" value={f.name_np} onChange={set('name_np')} /></Field>
        <Field label="PAN / VAT number"><input className="mono" maxLength={9} value={f.pan} onChange={set('pan')} /></Field>
        <Field label="Code" hint="Short handle, letters and dashes"><input className="mono" value={f.code} onChange={set('code')} /></Field>
        <Field label="What it sells"><input value={f.trade} onChange={set('trade')} /></Field>
        <Field label="Supplies">
          <select value={f.business_type} onChange={set('business_type')}>
            <option value="goods">Goods</option><option value="services">Services</option><option value="mixed">Goods and services</option>
          </select>
        </Field>
        <Field label="Address"><input value={f.address} onChange={set('address')} /></Field>
        <Field label="Phone"><input value={f.phone} onChange={set('phone')} /></Field>
        <Field label="Email"><input type="email" value={f.email} onChange={set('email')} /></Field>
      </div>
      <label className="check"><input type="checkbox" checked={f.vat_registered} onChange={(e) => setF({ ...f, vat_registered: e.target.checked })} /> VAT registered</label>
      <label className="check"><input type="checkbox" checked={f.is_active} onChange={(e) => setF({ ...f, is_active: e.target.checked })} /> Active (inactive businesses can't be opened by their staff)</label>
      <Button kind="primary" busy={busy} onClick={async () => {
        const r = await save(() => api(`platform/businesses/${b.id}`, { method: 'PATCH', body: f }), setError, setBusy)
        if (r) { setNotice('Saved'); onSaved() }
      }}>Save</Button>
    </section>
  )
}

interface Register { id: number; code: string; name: string; is_active: boolean; printer_driver: string }
interface BranchRow { id: number; name: string; code: string; timezone: string; address: string; is_active: boolean; registers: Register[] }

function Branches({ orgId }: { orgId: number }) {
  const rows = useLoad(() => api<BranchRow[]>(`platform/businesses/${orgId}/branches`), [orgId])
  const [adding, setAdding] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [nb, setNb] = useState({ name: '', code: '', address: '' })
  const patch = (url: string, body: object) => save(() => api(url, { method: 'PATCH', body }), setError, setBusy).then(() => rows.reload())
  return (
    <section className="card">
      <div className="card-head">
        <h2>Branches & registers</h2>
        <Button kind="primary" onClick={() => setAdding(true)}>Add branch</Button>
      </div>
      <p className="card-sub">A branch code starts every invoice number at that branch and can't be reused</p>
      <ErrorBox error={error || rows.error} />
      {rows.data?.map((b) => (
        <div key={b.id} className="branch-block">
          <div className="branch-block-head">
            <h3>{b.name}</h3><span className="mono muted">{b.code}</span>
            {!b.is_active && <Badge tone="bad">inactive</Badge>}
            <span className="spacer" />
            <Button kind="ghost" disabled={busy} onClick={() => void patch(`platform/branches/${b.id}`, { is_active: !b.is_active })}>
              {b.is_active ? 'Deactivate' : 'Reactivate'}
            </Button>
            <Button kind="ghost" disabled={busy} onClick={async () => {
              const n = b.registers.length + 1
              await save(() => api(`platform/branches/${b.id}/registers`, { body: { code: `R${n}`, name: `Till ${n}` } }), setError, setBusy)
              rows.reload()
            }}>Add register</Button>
          </div>
          <p className="muted small">{b.address || 'No address'} · {b.timezone}</p>
          <div className="flags">
            {b.registers.map((r) => (
              <button key={r.id} className="register-chip" title={r.is_active ? 'Deactivate this register' : 'Reactivate'}
                onClick={() => void patch(`platform/registers/${r.id}`, { is_active: !r.is_active })}>
                <span><strong>{r.name}</strong> <span className="mono muted">{r.code}</span></span>
                {!r.is_active && <Badge tone="bad">inactive</Badge>}
              </button>
            ))}
            {!b.registers.length && <span className="muted small">No registers</span>}
          </div>
        </div>
      ))}
      {rows.data && !rows.data.length && <div className="empty">No branches yet</div>}
      {adding && (
        <Modal title="Add branch" onClose={() => setAdding(false)}>
          <ErrorBox error={error} />
          <Field label="Branch name"><input value={nb.name} onChange={(e) => setNb({ ...nb, name: e.target.value })} /></Field>
          <Field label="Branch code" hint="Unique across every business"><input className="mono" maxLength={16} value={nb.code} onChange={(e) => setNb({ ...nb, code: e.target.value.toUpperCase() })} /></Field>
          <Field label="Address"><input value={nb.address} onChange={(e) => setNb({ ...nb, address: e.target.value })} /></Field>
          <p className="muted small">A first register (Till 1) is created with it</p>
          <div className="modal-foot">
            <Button onClick={() => setAdding(false)}>Cancel</Button>
            <Button kind="primary" busy={busy} onClick={async () => {
              const r = await save(() => api(`platform/businesses/${orgId}/branches`, { body: nb }), setError, setBusy)
              if (r) { setAdding(false); setNb({ name: '', code: '', address: '' }); rows.reload() }
            }}>Add branch</Button>
          </div>
        </Modal>
      )}
    </section>
  )
}

interface RulesData { cash_rounding_paisa: number; cashier_max_discount_bp: number; return_approval_threshold_paisa: number; stocktake_variance_threshold: number; updated_at: string }

function Rules({ orgId }: { orgId: number }) {
  const rules = useLoad(() => api<RulesData>(`platform/businesses/${orgId}/rules`), [orgId])
  if (!rules.data) return <ErrorBox error={rules.error} />
  return <RulesForm orgId={orgId} initial={rules.data} onSaved={rules.reload} />
}

function RulesForm({ orgId, initial, onSaved }: { orgId: number; initial: RulesData; onSaved: () => void }) {
  const [f, setF] = useState({
    rounding: String(initial.cash_rounding_paisa), discount: String(initial.cashier_max_discount_bp / 100),
    threshold: toRupeesInput(initial.return_approval_threshold_paisa), variance: String(initial.stocktake_variance_threshold),
  })
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  return (
    <section className="card">
      <h2>Shop rules</h2>
      <p className="card-sub">Apply to every branch of this business · last changed {fmtDate(initial.updated_at)}</p>
      <ErrorBox error={error} />
      {notice && <div className="alert alert-good">{notice}</div>}
      <div className="form-grid">
        <Field label="Cash rounding">
          <select value={f.rounding} onChange={(e) => setF({ ...f, rounding: e.target.value })}>
            <option value="1">No rounding</option><option value="50">Nearest 50 paisa</option><option value="100">Nearest rupee</option>
            <option value="500">Nearest 5 rupees</option><option value="1000">Nearest 10 rupees</option>
          </select>
        </Field>
        <Field label="Cashier discount limit (%)" hint="Above this a manager must approve"><input inputMode="decimal" value={f.discount} onChange={(e) => setF({ ...f, discount: e.target.value })} /></Field>
        <Field label="Return approval above (Rs)"><input inputMode="decimal" value={f.threshold} onChange={(e) => setF({ ...f, threshold: e.target.value })} /></Field>
        <Field label="Stocktake: reason needed above (units)"><input inputMode="numeric" value={f.variance} onChange={(e) => setF({ ...f, variance: e.target.value })} /></Field>
      </div>
      <Button kind="primary" busy={busy} onClick={async () => {
        const body = {
          cash_rounding_paisa: Number(f.rounding), cashier_max_discount_bp: Math.round(Number(f.discount) * 100),
          return_approval_threshold_paisa: toPaisa(f.threshold) ?? -1, stocktake_variance_threshold: Number(f.variance),
        }
        if (await save(() => api(`platform/businesses/${orgId}/rules`, { method: 'PATCH', body }), setError, setBusy)) { setNotice('Saved'); onSaved() }
      }}>Save rules</Button>
    </section>
  )
}

interface TaxData {
  invoice_series: 'branch' | 'register'; abbreviated_invoice_limit_paisa: number; require_buyer_pan_over_limit: boolean
  cbms_enabled: boolean; cbms_base_url: string; cbms_username: string; cbms_password_set: boolean; cbms_paused_reason: string
}

function Tax({ orgId }: { orgId: number }) {
  const tax = useLoad(() => api<TaxData>(`platform/businesses/${orgId}/tax-settings`), [orgId])
  if (!tax.data) return <ErrorBox error={tax.error} />
  return <TaxForm orgId={orgId} initial={tax.data} onSaved={tax.reload} />
}

function TaxForm({ orgId, initial, onSaved }: { orgId: number; initial: TaxData; onSaved: () => void }) {
  const [f, setF] = useState({ ...initial, limit: toRupeesInput(initial.abbreviated_invoice_limit_paisa), password: '' })
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  return (
    <section className="card">
      <h2>Tax & CBMS</h2>
      <p className="card-sub">The business's own IRD settings. Its admins see the full list under Tax & IRD in the IMS app</p>
      <ErrorBox error={error} />
      {notice && <div className="alert alert-good">{notice}</div>}
      {initial.cbms_paused_reason && <div className="alert alert-warn">CBMS paused: {initial.cbms_paused_reason}</div>}
      <div className="form-grid">
        <Field label="Invoice numbering">
          <select value={f.invoice_series} onChange={(e) => setF({ ...f, invoice_series: e.target.value as TaxData['invoice_series'] })}>
            <option value="branch">One series per branch</option><option value="register">One series per register</option>
          </select>
        </Field>
        <Field label="Abbreviated invoice limit (Rs)"><input inputMode="decimal" value={f.limit} onChange={(e) => setF({ ...f, limit: e.target.value })} /></Field>
        <Field label="CBMS address"><input className="mono" value={f.cbms_base_url} onChange={(e) => setF({ ...f, cbms_base_url: e.target.value })} /></Field>
        <Field label="CBMS username"><input value={f.cbms_username} onChange={(e) => setF({ ...f, cbms_username: e.target.value })} /></Field>
        <Field label="CBMS password" hint={initial.cbms_password_set ? 'Saved. Leave blank to keep it' : 'Not set'}>
          <input type="password" autoComplete="new-password" value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} />
        </Field>
      </div>
      <label className="check"><input type="checkbox" checked={f.cbms_enabled} onChange={(e) => setF({ ...f, cbms_enabled: e.target.checked })} /> Post bills to IRD CBMS</label>
      <label className="check"><input type="checkbox" checked={f.require_buyer_pan_over_limit} onChange={(e) => setF({ ...f, require_buyer_pan_over_limit: e.target.checked })} /> Require the buyer's PAN over the limit</label>
      <Button kind="primary" busy={busy} onClick={async () => {
        const body: Record<string, unknown> = {
          invoice_series: f.invoice_series, abbreviated_invoice_limit_paisa: toPaisa(f.limit) ?? 0, cbms_enabled: f.cbms_enabled,
          cbms_base_url: f.cbms_base_url, cbms_username: f.cbms_username, require_buyer_pan_over_limit: f.require_buyer_pan_over_limit,
        }
        if (f.password) body.cbms_password = f.password
        if (await save(() => api(`platform/businesses/${orgId}/tax-settings`, { method: 'PATCH', body }), setError, setBusy)) { setNotice('Saved'); onSaved() }
      }}>Save</Button>
    </section>
  )
}

interface Merchant { branch: number; branch_code: string; branch_name: string; enabled: boolean; merchant_code: string; username: string; password_set: boolean; secret_set: boolean; missing: string[]; status: string }
interface FonepayData { backend: 'disabled' | 'mock' | 'http'; base_url: string; mock_allowed: boolean; allowed_hosts: string[]; results: Merchant[] }

function Fonepay({ orgId }: { orgId: number }) {
  const fp = useLoad(() => api<FonepayData>(`platform/businesses/${orgId}/fonepay`), [orgId])
  const [mode, setMode] = useState<string | null>(null)
  const [url, setUrl] = useState<string | null>(null)
  const [editing, setEditing] = useState<Merchant | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  if (!fp.data) return <ErrorBox error={fp.error} />
  const d = fp.data
  return (
    <section className="card">
      <h2>FonePay</h2>
      <p className="card-sub">QR payments settle into each branch's own merchant account</p>
      <ErrorBox error={error} />
      <div className="form-grid">
        <Field label="Mode">
          <select value={mode ?? d.backend} onChange={(e) => setMode(e.target.value)}>
            <option value="disabled">Off</option><option value="http">Live</option>
            {d.mock_allowed && <option value="mock">Demo (simulated payments)</option>}
          </select>
        </Field>
        <Field label="API address" hint={`https on ${d.allowed_hosts.join(', ')}`}>
          <input className="mono" value={url ?? d.base_url} onChange={(e) => setUrl(e.target.value.trim())} />
        </Field>
      </div>
      <Button kind="primary" busy={busy} onClick={async () => {
        if (await save(() => api(`platform/businesses/${orgId}/fonepay`, { method: 'PATCH', body: { mode: mode ?? d.backend, base_url: url ?? d.base_url } }), setError, setBusy)) fp.reload()
      }}>Save</Button>
      <h3>Merchant per branch</h3>
      <table className="table compact">
        <thead><tr><th>Branch</th><th>Merchant code</th><th>Status</th><th /></tr></thead>
        <tbody>
          {d.results.map((m) => (
            <tr key={m.branch}>
              <td>{m.branch_name} <span className="mono muted">{m.branch_code}</span></td>
              <td className="mono">{m.merchant_code || '–'}</td>
              <td>{m.status === 'http' ? 'Live' : m.status === 'mock' ? 'Demo' : m.enabled && m.missing.length ? `Missing ${m.missing.join(', ')}` : 'Off'}</td>
              <td className="row-actions"><Button kind="ghost" onClick={() => setEditing(m)}>Edit</Button></td>
            </tr>
          ))}
        </tbody>
      </table>
      {editing && <MerchantForm orgId={orgId} m={editing} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); fp.reload() }} />}
    </section>
  )
}

function MerchantForm({ orgId, m, onClose, onSaved }: { orgId: number; m: Merchant; onClose: () => void; onSaved: () => void }) {
  const [f, setF] = useState({ enabled: m.enabled, merchant_code: m.merchant_code, username: m.username, password: '', secret: '' })
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  return (
    <Modal title={`FonePay merchant · ${m.branch_code}`} onClose={onClose}>
      <ErrorBox error={error} />
      <label className="check"><input type="checkbox" checked={f.enabled} onChange={(e) => setF({ ...f, enabled: e.target.checked })} /> Take FonePay at this branch</label>
      <Field label="Merchant code"><input className="mono" value={f.merchant_code} onChange={(e) => setF({ ...f, merchant_code: e.target.value.trim() })} /></Field>
      <Field label="API username"><input value={f.username} onChange={(e) => setF({ ...f, username: e.target.value })} /></Field>
      <Field label="API password" hint={m.password_set ? 'Saved. Leave blank to keep it' : 'Not set'}><input type="password" autoComplete="new-password" value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} /></Field>
      <Field label="Secret key" hint={m.secret_set ? 'Saved. Leave blank to keep it' : 'Not set'}><input type="password" autoComplete="new-password" value={f.secret} onChange={(e) => setF({ ...f, secret: e.target.value })} /></Field>
      <div className="modal-foot">
        <Button onClick={onClose}>Cancel</Button>
        <Button kind="primary" busy={busy} onClick={async () => {
          const body: Record<string, unknown> = { enabled: f.enabled, merchant_code: f.merchant_code, username: f.username }
          if (f.password) body.password = f.password
          if (f.secret) body.secret = f.secret
          if (await save(() => api(`platform/businesses/${orgId}/fonepay/merchants/${m.branch}`, { method: 'PATCH', body }), setError, setBusy)) onSaved()
        }}>Save</Button>
      </div>
    </Modal>
  )
}
