/** Tax & IRD (payment.md Steps 3–5): statutory reports with exports, CBMS
 * sync dashboard, turnover meter, settings and effective-dated tax rates. */
import { useState } from 'react'
import { useAuth } from '../auth'
import { Badge, Button, ErrorBox, Field, Modal, PageHead, Status, Tabs, fmtDate, useLoad } from '../components/ui'
import { api, qs } from '../lib/api'
import { npr, plain, toPaisa, toRupeesInput } from '../lib/money'

interface FiscalYears { current: string; current_month: number; years: string[]; months: { no: number; name: string }[]; today_bs: string }
interface Column { key: string; label: string; kind: 'text' | 'int' | 'money' | 'date' }
interface Report {
  title: string; subtitle: string; columns: Column[]; rows: Record<string, string | number>[]; totals: Record<string, number>
  notes: string[]; summary?: { output_vat: number; input_vat: number; net: number; nil_return: boolean; due_date_ad: string; due_date_bs: string }
}
interface Turnover {
  fiscal_year: string; turnover_paisa: number; cbms_threshold_paisa: number; cbms_mandatory_threshold_paisa: number
  vat_threshold_paisa: number; vat_registered: boolean; cbms_enabled: boolean; alerts: { level: 'warn' | 'bad'; message: string }[]
}

const REPORTS: [string, string][] = [
  ['sales-book', 'Sales book (Schedule-9)'], ['purchase-book', 'Purchase book (Schedule-8)'],
  ['sales-returns', 'Sales return register'], ['purchase-returns', 'Purchase return register'],
  ['vat-return', 'VAT return working'], ['cancelled-bills', 'Cancelled bills'],
  ['stock-reconciliation', 'Stock vs purchase book'], ['cbms-sync', 'CBMS sync'], ['audit-trail', 'Audit trail'],
]

export default function Compliance() {
  const { canOrg } = useAuth()
  const tabs: ['reports' | 'cbms' | 'turnover' | 'settings' | 'tax', string][] = [
    ['reports', 'Reports'], ['cbms', 'CBMS'], ['turnover', 'Turnover'],
  ]
  if (canOrg('branches.manage')) tabs.push(['settings', 'Settings'])
  if (canOrg('catalog.manage') || canOrg('catalog.view')) tabs.push(['tax', 'Tax categories'])
  const [tab, setTab] = useState<(typeof tabs)[number][0]>('reports')
  return (
    <div>
      <PageHead title="Tax & IRD" />
      <Tabs value={tab} onChange={setTab} tabs={tabs} />
      {tab === 'reports' && <Reports />}
      {tab === 'cbms' && <Cbms />}
      {tab === 'turnover' && <TurnoverMeter />}
      {tab === 'settings' && <Settings />}
      {tab === 'tax' && <TaxCategories />}
    </div>
  )
}

/** Report cells: the column header carries "Rs", and a zero amount is a quiet dash
 *  so the figures that exist stand out (ledger style). */
function fmtCell(v: string | number | undefined, kind: Column['kind']) {
  if (kind === 'money') {
    const n = Number(v ?? 0)
    return n ? plain(n) : <span className="zero" aria-label="zero">–</span>
  }
  return v ?? ''
}
const colLabel = (c: Column) => (c.kind === 'money' ? `${c.label} (Rs)` : c.label)

function Reports() {
  const { canOrg } = useAuth()
  const fys = useLoad(() => api<FiscalYears>('compliance/fiscal-years'), [])
  const [name, setName] = useState('sales-book')
  const [fy, setFy] = useState('')
  const [month, setMonth] = useState<string>('current')
  const [branchSel, setBranchSel] = useState('')
  const branches = useLoad(() => api<{ id: number; code: string; name: string }[]>('branches/directory/'), [])
  const effFy = fy || fys.data?.current || ''
  const effMonth = month === 'current' ? String(fys.data?.current_month ?? '') : month
  const params = { fy: effFy, month: effMonth || null, branch: canOrg('dashboard.view') ? branchSel || 'all' : null }
  const report = useLoad(() => (fys.data ? api<Report>(`compliance/reports/${name}${qs(params)}`) : Promise.resolve(null)),
    [name, effFy, effMonth, branchSel, fys.data])
  const r = report.data
  const exportUrl = (fmt: string) => `/api/v1/compliance/reports/${name}${qs({ ...params, export: fmt })}`

  return (
    <>
      <div className="toolbar">
        <select value={name} onChange={(e) => setName(e.target.value)} aria-label="Report">
          {REPORTS.map(([k, label]) => <option key={k} value={k}>{label}</option>)}
        </select>
        <select value={effFy} onChange={(e) => setFy(e.target.value)} aria-label="Fiscal year">
          {fys.data?.years.map((y) => <option key={y} value={y}>FY {y.replace('-', '/')}</option>)}
        </select>
        <select value={month} onChange={(e) => setMonth(e.target.value)} aria-label="Month">
          <option value="current">This month</option>
          <option value="">Whole year</option>
          {fys.data?.months.map((m) => <option key={m.no} value={m.no}>{m.name}</option>)}
        </select>
        {canOrg('dashboard.view') && (
          <select value={branchSel} onChange={(e) => setBranchSel(e.target.value)} aria-label="Branch">
            <option value="">All branches</option>
            {branches.data?.map((b) => <option key={b.id} value={b.id}>{b.code}</option>)}
          </select>
        )}
        <span className="spacer" />
        {['xlsx', 'pdf', 'xml', 'csv'].map((f) => (
          <a key={f} className="btn btn-default" href={exportUrl(f)}>{f === 'xlsx' ? 'Excel' : f.toUpperCase()}</a>
        ))}
        <a className="btn btn-primary" href={`/api/v1/compliance/reports/accountant-export${qs(params)}`}>Accountant export (zip)</a>
      </div>
      <ErrorBox error={report.error || fys.error} />
      {r && (
        <section className="card flush">
          <div className="report-head">
            <h2>{r.title}</h2>
            <div className="muted small">{r.subtitle}</div>
          </div>
          {r.summary && (
            <div className="tiles report-summary">
              <div className="tile"><div className="tile-label">Net output VAT</div><div className="tile-value">{npr(r.summary.output_vat)}</div></div>
              <div className="tile"><div className="tile-label">Net input VAT</div><div className="tile-value">{npr(r.summary.input_vat)}</div></div>
              <div className={`tile ${r.summary.net > 0 ? 'tile-warn' : ''}`}>
                <div className="tile-label">{r.summary.net >= 0 ? 'VAT payable' : 'Credit carried forward'}</div>
                <div className="tile-value">{npr(Math.abs(r.summary.net))}</div>
              </div>
              <div className="tile"><div className="tile-label">Due by</div><div className="tile-value">{r.summary.due_date_bs}</div>
                <div className="tile-sub">{r.summary.due_date_ad}{r.summary.nil_return ? ' · NIL return' : ''}</div></div>
            </div>
          )}
          <div className="table-scroll tall">
            <table className="table report-table">
              <thead><tr>{r.columns.map((c) => <th key={c.key} className={c.kind === 'money' || c.kind === 'int' ? 'num' : ''}>{colLabel(c)}</th>)}</tr></thead>
              <tbody>
                {r.rows.map((row, i) => (
                  <tr key={i}>{r.columns.map((c) => <td key={c.key} className={c.kind === 'money' || c.kind === 'int' ? 'num' : ''}>{fmtCell(row[c.key], c.kind)}</td>)}</tr>
                ))}
                {!r.rows.length && <tr><td colSpan={r.columns.length} className="empty">Nothing in this period</td></tr>}
              </tbody>
              {Object.keys(r.totals).length > 0 && (
                <tfoot><tr>{r.columns.map((c, i) => <th key={c.key} className={c.kind === 'money' || c.kind === 'int' ? 'num' : ''}>{i === 0 ? 'Total' : c.key in r.totals ? fmtCell(r.totals[c.key], c.kind) : ''}</th>)}</tr></tfoot>
              )}
            </table>
          </div>
          {r.notes.length > 0 && <ul className="notes">{r.notes.map((n) => <li key={n}>{n}</li>)}</ul>}
        </section>
      )}
    </>
  )
}

interface CbmsPost { id: number; doc_type: string; document_number: string; branch_code: string; issued_at: string; status: string; attempts: number; last_code: string; last_error: string; isrealtime: boolean | null }
interface CbmsStatus { enabled: boolean; paused_reason: string; pending: number; synced: number; failed: number; oldest_pending_at: string | null }

function Cbms() {
  const { canOrg } = useAuth()
  const [filter, setFilter] = useState<'pending' | 'failed' | 'synced'>('pending')
  const status = useLoad(() => api<CbmsStatus>('compliance/cbms/status/'), [])
  const posts = useLoad(() => api<{ results: CbmsPost[] }>(`compliance/cbms/${qs({ status: filter })}`), [filter])
  const [msg, setMsg] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const s = status.data
  const run = async (path: string) => {
    setErr(null)
    try {
      const r = await api<CbmsStatus & { posted?: number; requeued?: number }>(path, { method: 'POST' })
      setMsg(r.posted != null ? `Posted ${r.posted}` : `Re-queued ${r.requeued}`)
      status.reload()
      posts.reload()
    } catch (e) {
      setErr((e as Error).message)
    }
  }
  return (
    <>
      <ErrorBox error={err || status.error} />
      {s && !s.enabled && <div className="alert alert-warn">CBMS sync is <b>off</b>. Turn it on in Settings once the customer's CBMS login is set (required at Rs 20 crore turnover).</div>}
      {s?.paused_reason && <div className="alert alert-error"><b>Paused:</b> {s.paused_reason}</div>}
      {msg && <div className="alert alert-good">{msg}</div>}
      {s && (
        <div className="tiles">
          <div className={`tile ${s.pending ? 'tile-warn' : ''}`}><div className="tile-label">Pending</div><div className="tile-value">{s.pending}</div>
            {s.oldest_pending_at && <div className="tile-sub">oldest {fmtDate(s.oldest_pending_at)}</div>}</div>
          <div className="tile"><div className="tile-label">Synced</div><div className="tile-value">{s.synced}</div></div>
          <div className={`tile ${s.failed ? 'tile-bad' : ''}`}><div className="tile-label">Failed</div><div className="tile-value">{s.failed}</div></div>
        </div>
      )}
      <div className="toolbar">
        <Tabs value={filter} onChange={setFilter} tabs={[['pending', 'Pending'], ['failed', 'Failed'], ['synced', 'Synced']]} />
        <span className="spacer" />
        {canOrg('branches.manage') && <Button onClick={() => void run('compliance/cbms/sync-now/')}>Sync now</Button>}
        {canOrg('branches.manage') && !!s?.failed && <Button onClick={() => void run('compliance/cbms/retry-failed/')}>Retry failed</Button>}
      </div>
      <div className="card flush">
        <table className="table">
          <thead><tr><th>Document</th><th>Endpoint</th><th>Branch</th><th>Issued</th><th>Status</th><th>Code</th><th className="num">Attempts</th><th>Real-time</th><th>Error</th></tr></thead>
          <tbody>
            {posts.data?.results.map((p) => (
              <tr key={p.id}>
                <td className="mono">{p.document_number}</td><td>{p.doc_type}</td><td>{p.branch_code}</td><td>{fmtDate(p.issued_at)}</td>
                <td><Status value={p.status === 'synced' ? 'success' : p.status} /></td><td>{p.last_code}</td><td className="num">{p.attempts}</td>
                <td>{p.isrealtime == null ? '' : p.isrealtime ? 'yes' : 'no'}</td><td className="small">{p.last_error}</td>
              </tr>
            ))}
            {!posts.data?.results.length && <tr><td colSpan={9} className="empty">Nothing {filter}.</td></tr>}
          </tbody>
        </table>
      </div>
    </>
  )
}

export function TurnoverMeter() {
  const { data, error } = useLoad(() => api<Turnover>('compliance/turnover'), [])
  if (error) return <ErrorBox error={error} />
  if (!data) return null
  const bar = (label: string, limit: number) => {
    const pct = Math.min(100, (data.turnover_paisa * 100) / limit)
    return (
      <div className="meter">
        <div className="meter-head"><span>{label}</span><span>{npr(limit)}</span></div>
        <div className="meter-track" role="meter" aria-valuenow={Math.round(pct)} aria-valuemin={0} aria-valuemax={100} aria-label={label}>
          <span className={pct >= 100 ? 'meter-bad' : pct >= 80 ? 'meter-warn' : ''} style={{ width: `${Math.max(1, pct)}%` }} />
        </div>
        <div className="muted small">{pct.toFixed(1)}% used</div>
      </div>
    )
  }
  return (
    <section className="card">
      <h2>Turnover FY {data.fiscal_year.replace('-', '/')}: {npr(data.turnover_paisa)}</h2>
      <p className="muted small">Sales excluding VAT, net of credit/debit notes, all branches. CBMS sync is {data.cbms_enabled ? 'on' : 'off'}.</p>
      {data.alerts.map((a) => <div key={a.message} className={`alert alert-${a.level === 'bad' ? 'error' : 'warn'}`}>{a.message}</div>)}
      {bar('CBMS line (budget)', data.cbms_threshold_paisa)}
      {bar('CBMS mandatory (notice 2083/01/04)', data.cbms_mandatory_threshold_paisa)}
      {!data.vat_registered && bar('VAT registration threshold', data.vat_threshold_paisa)}
    </section>
  )
}

interface SettingsData {
  org_name: string; org_name_np: string; org_pan: string; org_address: string; org_phone: string; org_email: string
  org_vat_registered: boolean; org_business_type: string; invoice_series: 'branch' | 'register'
  abbreviated_invoice_limit_paisa: number; delivery_copy_limit_paisa: number; require_buyer_pan_over_limit: boolean
  digital_vat_discount_enabled: boolean; digital_vat_discount_bp: number; cbms_enabled: boolean; cbms_base_url: string
  cbms_username: string; cbms_password_set: boolean; cbms_realtime_window_minutes: number; cbms_paused_reason: string
  cbms_threshold_paisa: number; cbms_mandatory_threshold_paisa: number; vat_threshold_goods_paisa: number
  vat_threshold_services_paisa: number; excess_stock_fine_bp: number
}

function Settings() {
  const { data, error, setData } = useLoad(() => api<SettingsData>('compliance/settings'), [])
  const [password, setPassword] = useState('')
  const [msg, setMsg] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)
  if (!data) return <ErrorBox error={error} />
  const set = (patch: Partial<SettingsData>) => setData({ ...data, ...patch })
  const money = (k: keyof SettingsData) => (
    <input value={toRupeesInput(Number(data[k]))} onChange={(e) => { const p = toPaisa(e.target.value); if (p != null) set({ [k]: p } as Partial<SettingsData>) }} />
  )
  const save = async () => {
    setErr(null)
    setMsg(null)
    try {
      const body: Record<string, unknown> = { ...data }
      delete body.cbms_password_set
      delete body.cbms_paused_reason
      if (password) body.cbms_password = password
      setData(await api<SettingsData>('compliance/settings', { method: 'PATCH', body }))
      setPassword('')
      setMsg('Saved')
    } catch (e) {
      setErr((e as Error).message)
    }
  }
  return (
    <div className="settings">
      <ErrorBox error={err} />
      {msg && <div className="alert alert-good">{msg}</div>}
      <section className="card">
        <h2>Seller (printed on every tax invoice)</h2>
        <div className="form-grid">
          <Field label="Legal name"><input value={data.org_name} onChange={(e) => set({ org_name: e.target.value })} /></Field>
          <Field label="Name in Nepali"><input value={data.org_name_np} onChange={(e) => set({ org_name_np: e.target.value })} /></Field>
          <Field label="PAN / VAT no."><input value={data.org_pan} maxLength={9} onChange={(e) => set({ org_pan: e.target.value.replace(/\D/g, '') })} /></Field>
          <Field label="Address"><input value={data.org_address} onChange={(e) => set({ org_address: e.target.value })} /></Field>
          <Field label="Phone"><input value={data.org_phone} onChange={(e) => set({ org_phone: e.target.value })} /></Field>
          <Field label="Business type">
            <select value={data.org_business_type} onChange={(e) => set({ org_business_type: e.target.value })}>
              <option value="goods">Goods</option><option value="services">Services</option><option value="mixed">Mixed</option>
            </select>
          </Field>
          <label className="check"><input type="checkbox" checked={data.org_vat_registered} onChange={(e) => set({ org_vat_registered: e.target.checked })} /> VAT registered</label>
        </div>
      </section>
      <section className="card">
        <h2>Invoices</h2>
        <div className="form-grid">
          <Field label="Numbering" hint="IRD asks for one chronological series per branch and fiscal year">
            <select value={data.invoice_series} onChange={(e) => set({ invoice_series: e.target.value as 'branch' | 'register' })}>
              <option value="branch">Per branch + fiscal year (offline tills print provisional slips)</option>
              <option value="register">Per register + fiscal year (offline tills number locally)</option>
            </select>
          </Field>
          <Field label="Abbreviated invoice limit (Rs)">{money('abbreviated_invoice_limit_paisa')}</Field>
          <Field label="Delivery copy over (Rs)">{money('delivery_copy_limit_paisa')}</Field>
          <label className="check"><input type="checkbox" checked={data.require_buyer_pan_over_limit} onChange={(e) => set({ require_buyer_pan_over_limit: e.target.checked })} /> Require buyer PAN above the limit</label>
        </div>
      </section>
      <section className="card">
        <h2>Digital-payment VAT discount</h2>
        <div className="alert alert-warn small">Keep this OFF until IRD publishes the procedure (docs/ird-questions.md #2).</div>
        <div className="form-grid">
          <label className="check"><input type="checkbox" checked={data.digital_vat_discount_enabled} onChange={(e) => set({ digital_vat_discount_enabled: e.target.checked })} /> Enabled</label>
          <Field label="Share of VAT (%)"><input value={data.digital_vat_discount_bp / 100} onChange={(e) => set({ digital_vat_discount_bp: Math.round(Number(e.target.value) * 100) || 0 })} /></Field>
        </div>
      </section>
      <section className="card">
        <h2>CBMS (IRD Central Billing Monitoring System)</h2>
        {data.cbms_paused_reason && <div className="alert alert-error">{data.cbms_paused_reason}</div>}
        <div className="form-grid">
          <label className="check"><input type="checkbox" checked={data.cbms_enabled} onChange={(e) => set({ cbms_enabled: e.target.checked })} /> Send bills to CBMS</label>
          <Field label="Base URL" hint='Live: https://cbapi.ird.gov.np · test server from IRD · "mock://" for training'><input value={data.cbms_base_url} onChange={(e) => set({ cbms_base_url: e.target.value })} /></Field>
          <Field label="Username"><input value={data.cbms_username} onChange={(e) => set({ cbms_username: e.target.value })} /></Field>
          <Field label="Password" hint={data.cbms_password_set ? 'Stored (encrypted). Type to replace — e.g. after a portal password change.' : 'Not set'}>
            <input type="password" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} />
          </Field>
          <Field label="Real-time window (minutes)"><input type="number" value={data.cbms_realtime_window_minutes} onChange={(e) => set({ cbms_realtime_window_minutes: Number(e.target.value) })} /></Field>
        </div>
      </section>
      <section className="card">
        <h2>Thresholds</h2>
        <div className="form-grid">
          <Field label="CBMS line (Rs)">{money('cbms_threshold_paisa')}</Field>
          <Field label="CBMS mandatory (Rs)">{money('cbms_mandatory_threshold_paisa')}</Field>
          <Field label="VAT registration — goods (Rs)">{money('vat_threshold_goods_paisa')}</Field>
          <Field label="VAT registration — services (Rs)">{money('vat_threshold_services_paisa')}</Field>
          <Field label="Excess stock fine (%)"><input value={data.excess_stock_fine_bp / 100} onChange={(e) => set({ excess_stock_fine_bp: Math.round(Number(e.target.value) * 100) || 0 })} /></Field>
        </div>
      </section>
      <div className="toolbar"><Button kind="primary" onClick={() => void save()}>Save settings</Button></div>
    </div>
  )
}

interface TaxCat { id: number; code: string; name: string; kind: string; levy_type: string; is_active: boolean; current_vat_rate: string | null; current_levy_rate: string | null
  rates: { id: number; effective_from: string; vat_rate: string; levy_rate: string; note: string }[] }

function TaxCategories() {
  const { canOrg } = useAuth()
  const { data, error, reload } = useLoad(() => api<TaxCat[]>('compliance/tax-categories/'), [])
  const [adding, setAdding] = useState<TaxCat | 'new' | null>(null)
  return (
    <>
      <p className="muted">When the budget changes a rate, add a new rate with its effective date. Never edit history: past invoices keep the rate that applied.</p>
      <ErrorBox error={error} />
      {canOrg('catalog.manage') && <div className="toolbar"><Button kind="primary" onClick={() => setAdding('new')}>New category</Button></div>}
      <div className="card flush">
        <table className="table">
          <thead><tr><th>Code</th><th>Name</th><th>Kind</th><th>Levy</th><th className="num">VAT now</th><th>Rate history</th><th /></tr></thead>
          <tbody>
            {data?.map((c) => (
              <tr key={c.id}>
                <td className="mono">{c.code}</td><td>{c.name}</td>
                <td><Badge tone={c.kind === 'exempt' ? 'neutral' : c.kind === 'zero' ? 'info' : 'good'}>{c.kind}</Badge></td>
                <td>{c.levy_type ? `${c.levy_type.toUpperCase()} ${Number(c.current_levy_rate)}%` : ''}</td>
                <td className="num">{c.current_vat_rate != null ? `${Number(c.current_vat_rate)}%` : <span className="muted">not yet in force</span>}</td>
                <td className="small">{c.rates.map((r) => `${Number(r.vat_rate)}% from ${r.effective_from}${r.note ? ` (${r.note})` : ''}`).join(' · ')}</td>
                <td>{canOrg('catalog.manage') && <Button kind="ghost" onClick={() => setAdding(c)}>Add rate</Button>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {adding && <TaxForm cat={adding === 'new' ? null : adding} onClose={() => setAdding(null)} onSaved={() => { setAdding(null); reload() }} />}
    </>
  )
}

function TaxForm({ cat, onClose, onSaved }: { cat: TaxCat | null; onClose: () => void; onSaved: () => void }) {
  const [f, setF] = useState({ code: '', name: '', kind: 'standard', levy_type: '', vat_rate: '13', levy_rate: '0', effective_from: new Date().toISOString().slice(0, 10), note: '' })
  const [error, setError] = useState<string | null>(null)
  return (
    <Modal title={cat ? `New rate for ${cat.code}` : 'New tax category'} onClose={onClose}>
      <ErrorBox error={error} />
      {!cat && (
        <div className="form-grid">
          <Field label="Code"><input value={f.code} onChange={(e) => setF({ ...f, code: e.target.value.toUpperCase() })} /></Field>
          <Field label="Name"><input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
          <Field label="Kind">
            <select value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value })}>
              <option value="standard">Standard</option><option value="reduced">Reduced</option>
              <option value="zero">Zero-rated (keeps input credit)</option><option value="exempt">Exempt (no input credit)</option>
            </select>
          </Field>
          <Field label="Levy">
            <select value={f.levy_type} onChange={(e) => setF({ ...f, levy_type: e.target.value })}>
              <option value="">None</option><option value="hst">Health Service Tax</option><option value="esf">Education Service Fee</option>
            </select>
          </Field>
        </div>
      )}
      <div className="form-grid">
        <Field label="VAT rate (%)"><input value={f.vat_rate} onChange={(e) => setF({ ...f, vat_rate: e.target.value })} /></Field>
        {(cat?.levy_type || f.levy_type) && <Field label="Levy rate (%)"><input value={f.levy_rate} onChange={(e) => setF({ ...f, levy_rate: e.target.value })} /></Field>}
        <Field label="Effective from (AD)"><input type="date" value={f.effective_from} onChange={(e) => setF({ ...f, effective_from: e.target.value })} /></Field>
        <Field label="Legal basis / note"><input placeholder="Finance Act 2084 §…" value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} /></Field>
      </div>
      <div className="modal-foot">
        <Button onClick={onClose}>Cancel</Button>
        <Button kind="primary" onClick={async () => {
          try {
            const target = cat ?? await api<TaxCat>('compliance/tax-categories/', { body: { code: f.code, name: f.name, kind: f.kind, levy_type: f.levy_type } })
            await api(`compliance/tax-categories/${target.id}/rates/`, { body: { effective_from: f.effective_from, vat_rate: f.vat_rate, levy_rate: f.levy_rate, note: f.note } })
            onSaved()
          } catch (e) {
            setError((e as Error).message)
          }
        }}>Save</Button>
      </div>
    </Modal>
  )
}
