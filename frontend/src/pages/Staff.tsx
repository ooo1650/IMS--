import { useMemo, useState } from 'react'
import { Button, ErrorBox, Field, Guide, Modal, MoneyInput, PageHead, SkeletonRows, useLoad } from '../components/ui'
import { Tile } from '../components/stats'
import { Icon, initials } from '../components/Icon'
import { api } from '../lib/api'
import { moneyProblem, npr, plain, toPaisa, toRupeesInput } from '../lib/money'

/**
 * Staff & pay: who works at which branch and what they were paid, in place of
 * the salary spreadsheet. Owners/admins only (staff.manage). One month at a
 * time (BS); "Still to pay" = salary − deductions − salary paid.
 */

interface Row {
  id: number; name: string; position: string; phone: string; branch: number; branch_code: string; branch_name: string
  is_active: boolean; monthly_salary_paisa: number; salary_paid_paisa: number; bonus_paisa: number; advance_paisa: number
  deductions_paisa: number; due_paisa: number; advance_owed_paisa: number
}
interface Summary {
  month: string; month_label: string; current_month: string; not_paid: number
  months: { value: string; label: string }[]
  branches: { id: number; code: string; name: string }[]
  rows: Row[]
  totals: Record<'monthly_salary_paisa' | 'salary_paid_paisa' | 'due_paisa' | 'advance_owed_paisa' | 'paid_out_paisa', number>
}
interface StaffMember {
  id: number; branch: number; name: string; position: string; phone: string; joined_on: string | null
  monthly_salary_paisa: number; is_active: boolean; note: string
}
interface Payment {
  id: number; staff: number; staff_name: string; branch_code: string; kind: Kind; amount_paisa: number
  paid_on: string; paid_on_bs: string; month: string; month_label: string; method: string; note: string; created_by_name: string | null
}
type Kind = 'salary' | 'advance' | 'bonus' | 'deduction' | 'repayment'
type Show = 'all' | 'due' | 'owing'

const KINDS: [Kind, string][] = [['salary', 'Salary'], ['advance', 'Advance / loan'], ['bonus', 'Bonus / allowance'], ['deduction', 'Deduction'], ['repayment', 'Advance repaid']]
const KIND_LABEL = Object.fromEntries(KINDS) as Record<Kind, string>
const METHOD_LABEL: Record<string, string> = { cash: 'Cash', bank: 'Bank', wallet: 'Wallet', salary: 'Cut from salary' }
/** Money that goes out to the person; a deduction or repayment reduces what they get. */
const OUT: Kind[] = ['salary', 'advance', 'bonus']

function today() {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

/** Report figure: zero is a muted dash, the rest in the figure font. */
function Fig({ p, tone }: { p: number; tone?: 'warn' }) {
  if (!p) return <span className="muted">—</span>
  return <span className={tone === 'warn' ? 'text-warn' : undefined}>{plain(p)}</span>
}

export default function Staff() {
  const [month, setMonth] = useState('')
  const [branch, setBranch] = useState<number | null>(null)
  const [show, setShow] = useState<Show>('all')
  const [q, setQ] = useState('')
  const [paying, setPaying] = useState<{ staffId?: number } | null>(null)
  const [editing, setEditing] = useState<StaffMember | 'new' | null>(null)
  const [openId, setOpenId] = useState<number | null>(null)
  const [version, setVersion] = useState(0)
  const query = new URLSearchParams({ ...(month && { month }), ...(branch && { branch: String(branch) }) }).toString()
  const { data, error, loading, reload: load } = useLoad(() => api<Summary>(`staff-summary?${query}`), [query])
  const reload = () => { load(); setVersion((v) => v + 1) }
  const open = data?.rows.find((r) => r.id === openId) ?? null

  const needle = q.trim().toLowerCase()
  const rows = useMemo(() => (data?.rows ?? []).filter((r) =>
    (show === 'all' || (show === 'due' ? r.due_paisa > 0 : r.advance_owed_paisa > 0))
    && (!needle || `${r.name} ${r.position} ${r.phone} ${r.branch_code}`.toLowerCase().includes(needle))), [data, show, needle])
  const owing = (data?.rows ?? []).filter((r) => r.advance_owed_paisa > 0).length
  const label = data?.month_label ?? ''
  const exportUrl = (fmt: string) => `/api/v1/staff-export?${new URLSearchParams({ month: data?.month ?? '', export: fmt, ...(branch && { branch: String(branch) }) })}`
  const toggle = (s: Show) => setShow(show === s ? 'all' : s)

  return (
    <div>
      <PageHead title="Staff & pay" sub="Who works where, and what they were paid">
        {data && <a className="btn btn-default" href={exportUrl('xlsx')} title={`Every entry for ${label}, as an Excel sheet`}>Export Excel</a>}
        <Button onClick={() => setEditing('new')}><Icon name="plus" size={16} /> Add staff</Button>
        <Button kind="primary" disabled={!data?.rows.length} why="Add staff first" onClick={() => setPaying({})}>
          <Icon name="wallet" size={16} /> Record payment
        </Button>
      </PageHead>
      <Guide id="staff" title="Using Staff & pay">
        <ol>
          <li><strong>Add staff</strong> once, with their branch and monthly salary (or load your Excel list in <strong>Import</strong>).</li>
          <li>Each time money changes hands, <strong>Record payment</strong>: salary, advance, bonus, a deduction, or an advance paid back.</li>
          <li><strong>Still to pay</strong> shows who hasn't had their full salary this month. Click a person to see everything recorded for them.</li>
        </ol>
        <p className="muted small">Only owners and admins can see this page.</p>
      </Guide>

      <div className="toolbar">
        <select aria-label="Month" value={data?.month ?? month} onChange={(e) => setMonth(e.target.value)}>
          {(data?.months ?? []).map((m) => <option key={m.value} value={m.value}>{m.label}{m.value === data?.current_month ? ' (this month)' : ''}</option>)}
        </select>
        {(data?.branches.length ?? 0) > 1 && (
          <div className="chips" role="group" aria-label="Branch">
            <button className={`chip ${branch == null ? 'active' : ''}`} onClick={() => setBranch(null)}>All branches</button>
            {data!.branches.map((b) => (
              <button key={b.id} className={`chip ${branch === b.id ? 'active' : ''}`} title={b.name} onClick={() => setBranch(b.id)}>{b.code}</button>
            ))}
          </div>
        )}
        <span className="spacer" />
        <span className="input-icon search">
          <Icon name="search" size={16} />
          <input placeholder="Search name, job or phone" value={q} onChange={(e) => setQ(e.target.value)} />
        </span>
      </div>
      <ErrorBox error={error} />

      {data && (
        <div className="tiles">
          <Tile label={`Paid out in ${label}`} value={npr(data.totals.paid_out_paisa)} sub="Salary, bonus and advances" />
          <Tile label="Still to pay" value={npr(data.totals.due_paisa)} tone={data.not_paid ? 'warn' : undefined}
            sub={data.not_paid ? `${data.not_paid} ${data.not_paid === 1 ? 'person' : 'people'} not fully paid` : 'Everyone paid'}
            onClick={data.not_paid ? () => toggle('due') : undefined} action={show === 'due' ? 'Show everyone' : 'Show them'} active={show === 'due'} />
          <Tile label="Advances owed" value={npr(data.totals.advance_owed_paisa)} sub={owing ? `By ${owing} ${owing === 1 ? 'person' : 'people'}` : 'Nothing owed'}
            onClick={owing ? () => toggle('owing') : undefined} action={show === 'owing' ? 'Show everyone' : 'Show them'} active={show === 'owing'} />
          <Tile label="Staff" value={data.rows.filter((r) => r.is_active).length} sub={`Monthly salaries ${npr(data.totals.monthly_salary_paisa)}`} />
        </div>
      )}

      <div className="card flush">
        <div className="table-scroll">
          <table className="table">
            <thead>
              <tr>
                <th>Staff</th><th>Branch</th><th className="num">Salary (Rs)</th><th className="num">Paid (Rs)</th>
                <th className="num">Cut (Rs)</th><th className="num">Still to pay (Rs)</th><th className="num">Advance owed (Rs)</th><th />
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id} className="clickable" onClick={() => setOpenId(r.id)}>
                  <td>
                    <div className="cell-user">
                      <span className="avatar avatar-sm">{initials(r.name)}</span>
                      <span><span className="cell-strong">{r.name}</span>{!r.is_active && <span className="muted small"> · left</span>}
                        {r.position && <span className="muted small"><br />{r.position}</span>}</span>
                    </div>
                  </td>
                  <td title={r.branch_name}>{r.branch_code}</td>
                  <td className="num"><Fig p={r.monthly_salary_paisa} /></td>
                  <td className="num"><Fig p={r.salary_paid_paisa} /></td>
                  <td className="num"><Fig p={r.deductions_paisa} /></td>
                  <td className="num"><Fig p={r.due_paisa} tone="warn" /></td>
                  <td className="num"><Fig p={r.advance_owed_paisa} /></td>
                  <td className="row-actions">
                    <Button kind="ghost" onClick={(e) => { e.stopPropagation(); setPaying({ staffId: r.id }) }}>Pay</Button>
                  </td>
                </tr>
              ))}
              {!data && loading && <SkeletonRows cols={8} />}
              {data && !rows.length && (
                <tr><td colSpan={8} className="empty">
                  {data.rows.length ? 'Nobody matches.' : <>No staff yet. <button className="link" onClick={() => setEditing('new')}>Add the first person</button>.</>}
                </td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {paying && data && (
        <PaymentForm summary={data} staffId={paying.staffId} onClose={() => setPaying(null)} onSaved={() => { setPaying(null); reload() }} />
      )}
      {editing && data && (
        <StaffForm branches={data.branches} staff={editing === 'new' ? null : editing} defaultBranch={branch}
          onClose={() => setEditing(null)} onSaved={() => { setEditing(null); reload() }} />
      )}
      {open && data && !paying && !editing && (
        <StaffDetail key={version} row={open} summary={data} onClose={() => setOpenId(null)} onPay={() => setPaying({ staffId: open.id })}
          onEdit={(s) => setEditing(s)} onChanged={reload} />
      )}
    </div>
  )
}

function PaymentForm({ summary, staffId, payment, onClose, onSaved }: {
  summary: Summary; staffId?: number; payment?: Payment; onClose: () => void; onSaved: () => void
}) {
  const listed = summary.rows.some((r) => r.id === payment?.staff) || !payment ? summary.rows
    : [...summary.rows, { id: payment.staff, name: payment.staff_name, branch_code: payment.branch_code } as Row]
  const people = [...listed].sort((a, b) => a.branch_code.localeCompare(b.branch_code) || a.name.localeCompare(b.name))
  const [staff, setStaff] = useState(String(payment?.staff ?? staffId ?? ''))
  const person = people.find((p) => String(p.id) === staff)
  const [kind, setKind] = useState<Kind>(payment?.kind ?? 'salary')
  const suggest = (k: Kind, p = person) => (k === 'salary' && p?.due_paisa ? toRupeesInput(p.due_paisa) : '')
  const [amount, setAmount] = useState(payment ? toRupeesInput(payment.amount_paisa) : suggest('salary'))
  const [paidOn, setPaidOn] = useState(payment?.paid_on ?? today())
  const [month, setMonth] = useState(payment?.month ?? summary.month)
  const [method, setMethod] = useState(payment?.method || 'cash')
  const [note, setNote] = useState(payment?.note ?? '')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const methods = kind === 'deduction' ? [] : ['cash', 'bank', 'wallet', ...(kind === 'repayment' ? ['salary'] : [])]
  const months = summary.months.some((m) => m.value === month) ? summary.months : [{ value: month, label: payment?.month_label ?? month }, ...summary.months]
  const problem = amount ? moneyProblem(amount) : null
  const paisa = toPaisa(amount)
  const missing = !staff ? 'Choose who' : !paisa ? 'Enter the amount' : !paidOn ? 'Enter the date' : null

  const pickKind = (k: Kind) => {
    setKind(k)
    if (!methods.length || (method === 'salary' && k !== 'repayment')) setMethod('cash')
    if (!payment && (!amount || amount === suggest(kind))) setAmount(suggest(k))
  }
  const save = async () => {
    setError(null)
    setBusy(true)
    try {
      const body = { staff: Number(staff), kind, amount_paisa: paisa, paid_on: paidOn, month, method: kind === 'deduction' ? '' : method, note }
      await api(payment ? `staff-payments/${payment.id}/` : 'staff-payments/', { method: payment ? 'PATCH' : 'POST', body })
      onSaved()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal title={payment ? 'Correct entry' : 'Record payment'} onClose={onClose}>
      <ErrorBox error={error} />
      <Field label="Staff">
        <select autoFocus={!staff} value={staff} onChange={(e) => {
          setStaff(e.target.value)
          const p = people.find((x) => String(x.id) === e.target.value)
          if (!payment && (!amount || amount === suggest(kind))) setAmount(suggest(kind, p))
        }}>
          <option value="">Choose…</option>
          {people.map((p) => <option key={p.id} value={p.id}>{p.name} · {p.branch_code}</option>)}
        </select>
      </Field>
      {person && (person.due_paisa > 0 || person.advance_owed_paisa > 0) && (
        <p className="muted small">
          {person.due_paisa > 0 && <>Still to pay in {summary.month_label}: <span className="fig">{npr(person.due_paisa)}</span>. </>}
          {person.advance_owed_paisa > 0 && <>Advance owed: <span className="fig">{npr(person.advance_owed_paisa)}</span>.</>}
        </p>
      )}
      <Field label="Type">
        <div className="chips" role="group" aria-label="Type">
          {KINDS.map(([k, l]) => <button key={k} type="button" className={`chip ${kind === k ? 'active' : ''}`} onClick={() => pickKind(k)}>{l}</button>)}
        </div>
      </Field>
      <div className="form-grid">
        <Field label="Amount (Rs)" error={problem}>
          <MoneyInput autoFocus={!!staff} value={amount} onChange={setAmount} placeholder="0.00" />
        </Field>
        <Field label="Date paid"><input type="date" value={paidOn} onChange={(e) => setPaidOn(e.target.value)} /></Field>
        <Field label="For month" hint={kind === 'salary' ? 'Which month this salary is for' : undefined}>
          <select value={month} onChange={(e) => setMonth(e.target.value)}>
            {months.map((m) => <option key={m.value} value={m.value}>{m.label}</option>)}
          </select>
        </Field>
      </div>
      {methods.length > 0 && (
        <Field label={kind === 'repayment' ? 'Paid back by' : 'Paid by'}>
          <div className="chips" role="group" aria-label="Paid by">
            {methods.map((m) => <button key={m} type="button" className={`chip ${method === m ? 'active' : ''}`} onClick={() => setMethod(m)}>{METHOD_LABEL[m]}</button>)}
          </div>
        </Field>
      )}
      <Field label="Note" hint={kind === 'deduction' ? 'Why, e.g. 2 days absent' : undefined}>
        <input value={note} maxLength={255} onChange={(e) => setNote(e.target.value)} placeholder="Optional" />
      </Field>
      <div className="modal-foot">
        <Button onClick={onClose}>Cancel</Button>
        <Button kind="primary" busy={busy} disabled={!!missing || !!problem} why={missing ?? problem} onClick={() => void save()}>
          {payment ? 'Save' : 'Record'}
        </Button>
      </div>
    </Modal>
  )
}

function StaffForm({ branches, staff, defaultBranch, onClose, onSaved }: {
  branches: Summary['branches']; staff: StaffMember | null; defaultBranch: number | null; onClose: () => void; onSaved: () => void
}) {
  const [f, setF] = useState({
    name: staff?.name ?? '', branch: String(staff?.branch ?? defaultBranch ?? (branches.length === 1 ? branches[0].id : '')),
    position: staff?.position ?? '', phone: staff?.phone ?? '', joined_on: staff?.joined_on ?? '', note: staff?.note ?? '',
  })
  const [salary, setSalary] = useState(staff?.monthly_salary_paisa ? toRupeesInput(staff.monthly_salary_paisa) : '')
  const [active, setActive] = useState(staff?.is_active ?? true)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const set = (k: keyof typeof f, v: string) => setF({ ...f, [k]: v })
  const problem = salary ? moneyProblem(salary) : null
  const missing = !f.name.trim() ? 'Enter a name' : !f.branch ? 'Choose a branch' : null
  const save = async () => {
    setError(null)
    setBusy(true)
    try {
      const body = { ...f, branch: Number(f.branch), joined_on: f.joined_on || null, monthly_salary_paisa: salary ? toPaisa(salary) : 0, is_active: active }
      await api(staff ? `staff/${staff.id}/` : 'staff/', { method: staff ? 'PATCH' : 'POST', body })
      onSaved()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <Modal title={staff ? `Edit ${staff.name}` : 'Add staff'} onClose={onClose}>
      <ErrorBox error={error} />
      <div className="form-grid">
        <Field label="Name"><input autoFocus value={f.name} maxLength={120} onChange={(e) => set('name', e.target.value)} /></Field>
        <Field label="Branch">
          <select value={f.branch} onChange={(e) => set('branch', e.target.value)}>
            <option value="">Choose…</option>
            {branches.map((b) => <option key={b.id} value={b.id}>{b.name} ({b.code})</option>)}
          </select>
        </Field>
        <Field label="Job" hint="e.g. Cashier, Helper"><input value={f.position} maxLength={80} onChange={(e) => set('position', e.target.value)} /></Field>
        <Field label="Phone"><input value={f.phone} maxLength={40} inputMode="tel" onChange={(e) => set('phone', e.target.value)} /></Field>
        <Field label="Monthly salary (Rs)" error={problem}><MoneyInput value={salary} onChange={setSalary} placeholder="0.00" /></Field>
        <Field label="Joined on"><input type="date" value={f.joined_on} onChange={(e) => set('joined_on', e.target.value)} /></Field>
      </div>
      <Field label="Note"><input value={f.note} maxLength={255} placeholder="Optional" onChange={(e) => set('note', e.target.value)} /></Field>
      {staff && (
        <label className="check">
          <input type="checkbox" checked={active} onChange={(e) => setActive(e.target.checked)} />
          Still working here <span className="muted small">(untick when they leave; their history stays)</span>
        </label>
      )}
      <div className="modal-foot">
        <Button onClick={onClose}>Cancel</Button>
        <Button kind="primary" busy={busy} disabled={!!missing || !!problem} why={missing ?? problem} onClick={() => void save()}>{staff ? 'Save' : 'Add staff'}</Button>
      </div>
    </Modal>
  )
}

function StaffDetail({ row, summary, onClose, onPay, onEdit, onChanged }: {
  row: Row; summary: Summary; onClose: () => void; onPay: () => void; onEdit: (s: StaffMember) => void; onChanged: () => void
}) {
  const { data: person } = useLoad(() => api<StaffMember>(`staff/${row.id}/`), [row.id])
  const history = useLoad(() => api<Payment[]>(`staff-payments/?staff=${row.id}`), [row.id])
  const [fixing, setFixing] = useState<Payment | null>(null)
  const [error, setError] = useState<string | null>(null)
  const remove = async (p: Payment) => {
    if (!window.confirm(`Remove this ${KIND_LABEL[p.kind].toLowerCase()} of ${npr(p.amount_paisa)} (${p.paid_on_bs})?`)) return
    setError(null)
    try {
      await api(`staff-payments/${p.id}/`, { method: 'DELETE' })
      history.reload()
      onChanged()
    } catch (e) {
      setError((e as Error).message)
    }
  }
  if (fixing) {
    return <PaymentForm summary={summary} payment={fixing} onClose={() => setFixing(null)}
      onSaved={() => { setFixing(null); history.reload(); onChanged() }} />
  }
  return (
    <Modal title={row.name} onClose={onClose} wide>
      <div className="detail-head">
        <span className="muted">{[row.position, row.branch_name, row.phone].filter(Boolean).join(' · ')}</span>
        <span className="spacer" />
        {person && <Button onClick={() => onEdit(person)}>Edit details</Button>}
        <Button kind="primary" onClick={onPay}><Icon name="wallet" size={16} /> Record payment</Button>
      </div>
      <p className="small">
        Salary <span className="fig">{npr(row.monthly_salary_paisa)}</span> a month
        {row.advance_owed_paisa > 0 && <> · advance owed <span className="fig text-warn">{npr(row.advance_owed_paisa)}</span></>}
        {person?.joined_on && <> · joined {person.joined_on}</>}
      </p>
      <ErrorBox error={error ?? history.error} />
      <div className="table-scroll tall">
        <table className="table">
          <thead><tr><th>Date (BS)</th><th>For</th><th>Type</th><th>Paid by</th><th className="num">Amount (Rs)</th><th>Note</th><th /></tr></thead>
          <tbody>
            {(history.data ?? []).map((p) => (
              <tr key={p.id}>
                <td className="fig" title={p.paid_on}>{p.paid_on_bs}</td>
                <td>{p.month_label}</td>
                <td>{KIND_LABEL[p.kind]}</td>
                <td>{METHOD_LABEL[p.method] ?? <span className="muted">—</span>}</td>
                <td className="num">{OUT.includes(p.kind) ? plain(p.amount_paisa) : <span className="muted">−{plain(p.amount_paisa)}</span>}</td>
                <td className="details">{p.note || <span className="muted">—</span>}</td>
                <td className="row-actions">
                  <Button kind="ghost" onClick={() => setFixing(p)}>Correct</Button>
                  <Button kind="ghost" onClick={() => void remove(p)} aria-label="Remove entry"><Icon name="trash" size={16} /></Button>
                </td>
              </tr>
            ))}
            {!history.data && history.loading && <SkeletonRows cols={7} rows={4} />}
            {history.data && !history.data.length && <tr><td colSpan={7} className="empty">Nothing recorded yet.</td></tr>}
          </tbody>
        </table>
      </div>
    </Modal>
  )
}
