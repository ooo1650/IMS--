import { useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { useAuth } from '../auth'
import { Icon, initials } from '../components/Icon'
import { ProductPicker } from '../components/ProductPicker'
import { Button, ErrorBox, Field, fmtDate, Guide, LoadMore, Modal, PageHead, QtyInput, Status, Tabs, useLoad, usePaged, ViewOnly } from '../components/ui'
import { api, qs } from '../lib/api'
import { moneyProblem, npr, toPaisa, toRupeesInput } from '../lib/money'

interface POLine { id: number; product: number; sku: string; name: string; qty_ordered: number; qty_received: number; unit_cost_paisa: number }
interface PO {
  id: number; supplier: number; supplier_name: string; status: string; created_at: string; ordered_at: string | null
  expected_at: string | null; received_at: string | null; note: string; lines: POLine[]; total_paisa: number
  receipts: { id: number; received_at: string; received_bs: string; by_name: string; supplier_invoice_ref: string; supplier_invoice_bs: string
    vat_paisa: number; claimable_vat_paisa: number; lines: { product: string; qty: number; unit_cost_paisa: number }[] }[]
}

export default function Purchasing() {
  const { branch, can, canOrg } = useAuth()
  const [tab, setTab] = useState<'open' | 'all' | 'returns' | 'suppliers'>('open')
  const { items, loading, error, reload, more } = usePaged<PO>(tab === 'returns' || tab === 'suppliers' ? null : `purchase-orders/${qs({ status: tab === 'open' ? 'draft,ordered,partial' : null, b: branch?.id })}`)
  const manage = can('purchasing.manage')
  // Arriving from Inventory → "Order these": open a new PO already holding those items.
  const location = useLocation()
  const navigate = useNavigate()
  const [prefill] = useState(() => (location.state as { reorder?: Draft['lines']; supplier?: number | null } | null) ?? null)
  const [editing, setEditing] = useState<PO | 'new' | null>(() => (manage && prefill?.reorder?.length ? 'new' : null))
  const [open, setOpen] = useState<PO | null>(null)
  const closeForm = () => {
    setEditing(null)
    if (location.state) navigate(location.pathname, { replace: true, state: null }) // a reload won't reopen it
  }
  return (
    <div>
      <PageHead title="Purchasing" sub="Purchase orders, receiving, returns and suppliers">
        {manage && tab !== 'suppliers' && <Button kind="primary" onClick={() => setEditing('new')}><Icon name="plus" size={16} /> New PO</Button>}
      </PageHead>
      {!manage && <ViewOnly>Creating and receiving purchase orders needs a purchasing role at this branch. Ask your manager.</ViewOnly>}
      <Guide id="purchasing" title="Buying stock">
        <ol>
          <li>Add your suppliers on the <strong>Suppliers</strong> tab.</li>
          <li>Create a purchase order with the items, quantities and costs, then <strong>Mark ordered</strong>.</li>
          <li>When the goods arrive, <strong>Receive goods</strong>: stock goes up at this branch and the purchase VAT is recorded for your VAT return.</li>
        </ol>
        <p>Sending goods back? Open the order → <strong>Return goods to supplier</strong>.</p>
      </Guide>
      <Tabs value={tab} onChange={setTab} tabs={[['open', 'Open orders'], ['all', 'All orders'], ['returns', 'Purchase returns'], ['suppliers', 'Suppliers']]} />
      {tab === 'suppliers' ? <Suppliers manage={manage || canOrg('catalog.manage')} /> : tab === 'returns' ? <PurchaseReturns manage={manage} /> : (<>
      <ErrorBox error={error} />
      <div className="card flush">
        <table className="table">
          <thead><tr><th>PO</th><th>Supplier</th><th>Status</th><th className="num">Ordered value</th><th>Created</th><th>Expected</th></tr></thead>
          <tbody>
            {items.map((po) => (
              <tr key={po.id} className="clickable" onClick={() => setOpen(po)}>
                <td className="cell-strong">PO-{po.id}</td>
                <td>{po.supplier_name}</td>
                <td><Status value={po.status} /></td>
                <td className="num">{npr(po.total_paisa)}</td>
                <td>{fmtDate(po.created_at)}</td>
                <td>{po.expected_at ?? '—'}</td>
              </tr>
            ))}
            {!items.length && !loading && <tr><td colSpan={6} className="empty">No purchase orders</td></tr>}
          </tbody>
        </table>
        <LoadMore more={more} loading={loading} />
      </div>
      </>)}
      {editing && (
        <POForm po={editing === 'new' ? null : editing}
          draft={editing === 'new' && prefill?.reorder ? { lines: prefill.reorder, supplier: prefill.supplier ?? null } : undefined}
          onClose={closeForm} onSaved={() => { closeForm(); reload() }} />
      )}
      {open && <PODetail po={open} manage={manage} onEdit={() => { setEditing(open); setOpen(null) }} onClose={() => { setOpen(null); reload() }} />}
    </div>
  )
}

interface Draft { lines: { product: number; name: string; qty: number; cost: string }[]; supplier: number | null }

function POForm({ po, draft, onClose, onSaved }: { po: PO | null; draft?: Draft; onClose: () => void; onSaved: () => void }) {
  const suppliers = useLoad(() => api<{ id: number; name: string }[]>('suppliers/'), [])
  const [supplier, setSupplier] = useState(po ? String(po.supplier) : draft?.supplier ? String(draft.supplier) : '')
  const [newSupplier, setNewSupplier] = useState('')
  const [newSupplierPan, setNewSupplierPan] = useState('')
  const [expected, setExpected] = useState(po?.expected_at ?? '')
  const [note, setNote] = useState(po?.note ?? '')
  const [lines, setLines] = useState(po?.lines.map((l) => ({ product: l.product, name: l.name, qty: l.qty_ordered, cost: toRupeesInput(l.unit_cost_paisa) })) ?? draft?.lines ?? [])
  const [error, setError] = useState<string | null>(null)

  async function save() {
    setError(null)
    try {
      let sup = supplier
      if (!sup) throw new Error('Choose a supplier (or add a new one)')
      if (!lines.length) throw new Error('Add at least one product')
      if (sup === 'new') sup = String((await api<{ id: number }>('suppliers/', { body: { name: newSupplier, pan: newSupplierPan } })).id)
      const body = {
        supplier: Number(sup), expected_at: expected || null, note,
        lines: lines.map((l) => {
          const c = toPaisa(l.cost)
          if (c == null) throw new Error(`Cost for ${l.name}: ${moneyProblem(l.cost) ?? 'enter the cost per unit'}`)
          return { product: l.product, qty_ordered: l.qty, unit_cost_paisa: c }
        }),
      }
      await api(po ? `purchase-orders/${po.id}/` : 'purchase-orders/', { method: po ? 'PUT' : 'POST', body })
      onSaved()
    } catch (e) {
      setError((e as Error).message)
    }
  }

  return (
    <Modal title={po ? `Edit PO-${po.id}` : 'New purchase order'} onClose={onClose} wide>
      <ErrorBox error={error} />
      {!po && draft && (
        <div className="alert alert-info">
          Started from Inventory with {draft.lines.length} product{draft.lines.length === 1 ? '' : 's'} that need restocking.
          Quantities are each product's reorder quantity (or enough for twice its reorder point) and costs are the current
          average cost: check them against the supplier's prices before saving.
        </div>
      )}
      <div className="form-grid">
        <Field label="Supplier">
          <select value={supplier} onChange={(e) => setSupplier(e.target.value)}>
            <option value="">Choose…</option>
            {suppliers.data?.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
            <option value="new">+ New supplier…</option>
          </select>
        </Field>
        {supplier === 'new' && <Field label="New supplier name"><input value={newSupplier} onChange={(e) => setNewSupplier(e.target.value)} /></Field>}
        {supplier === 'new' && <Field label="Supplier PAN (9 digits)"><input value={newSupplierPan} maxLength={9} onChange={(e) => setNewSupplierPan(e.target.value.replace(/\D/g, ''))} /></Field>}
        <Field label="Expected date"><input type="date" value={expected} onChange={(e) => setExpected(e.target.value)} /></Field>
        <Field label="Note"><input value={note} onChange={(e) => setNote(e.target.value)} /></Field>
      </div>
      <ProductPicker onPick={(p) => setLines((ls) => (ls.some((l) => l.product === p.id) ? ls : [...ls, { product: p.id, name: p.name, qty: Math.max(1, 1), cost: toRupeesInput(p.cost_price_paisa ?? 0) }]))} />
      <table className="table">
        <thead><tr><th>Product</th><th className="num">Qty</th><th className="num">Unit cost (Rs)</th><th /></tr></thead>
        <tbody>
          {lines.map((l, i) => (
            <tr key={l.product}>
              <td>{l.name}</td>
              <td className="num"><QtyInput min={1} aria-label={`Quantity of ${l.name}`} value={l.qty} onChange={(n) => setLines(lines.map((x, j) => (j === i ? { ...x, qty: n } : x)))} /></td>
              <td className="num"><input className="input-sm" value={l.cost} onChange={(e) => setLines(lines.map((x, j) => (j === i ? { ...x, cost: e.target.value } : x)))} /></td>
              <td><Button kind="ghost" onClick={() => setLines(lines.filter((_, j) => j !== i))}>Remove</Button></td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="modal-foot">
        <Button onClick={onClose}>Cancel</Button>
        <Button kind="primary" disabled={!supplier || !lines.length} why={!supplier ? 'Choose a supplier to save' : 'Add at least one product to save'}
          onClick={() => void save()}>Save draft</Button>
      </div>
    </Modal>
  )
}

function PODetail({ po: initial, manage, onEdit, onClose }: { po: PO; manage: boolean; onEdit: () => void; onClose: () => void }) {
  const [po, setPo] = useState(initial)
  const [recv, setRecv] = useState<Record<number, { qty: string; cost: string }>>({})
  const [invoice, setInvoice] = useState('')
  const [invoiceDate, setInvoiceDate] = useState(new Date().toISOString().slice(0, 10))
  const [isImport, setIsImport] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const receiving = manage && ['ordered', 'partial'].includes(po.status)
  // Default each line to "receive everything outstanding at the ordered cost".
  const row = (l: POLine) => recv[l.id] ?? { qty: String(l.qty_ordered - l.qty_received), cost: toRupeesInput(l.unit_cost_paisa) }
  const act = async (path: string, body: unknown = {}) => {
    setError(null)
    try {
      setPo(await api<PO>(`purchase-orders/${po.id}/${path}/`, { body }))
      setRecv({})
    } catch (e) {
      setError((e as Error).message)
    }
  }
  return (
    <Modal title={`PO-${po.id} · ${po.supplier_name}`} onClose={onClose} wide>
      <ErrorBox error={error} />
      <div className="meta-row"><Status value={po.status} /><span>Created {fmtDate(po.created_at)}</span>{po.ordered_at && <span>Ordered {fmtDate(po.ordered_at)}</span>}</div>
      <table className="table">
        <thead>
          <tr><th>Product</th><th className="num">Ordered</th><th className="num">Received</th><th className="num">Ordered cost</th>{receiving && <><th className="num">Receive now</th><th className="num">Invoice cost (Rs)</th></>}</tr>
        </thead>
        <tbody>
          {po.lines.map((l) => (
            <tr key={l.id}>
              <td>{l.name} <span className="muted small">{l.sku}</span></td>
              <td className="num">{l.qty_ordered}</td>
              <td className="num">{l.qty_received}</td>
              <td className="num">{npr(l.unit_cost_paisa)}</td>
              {receiving && (
                <>
                  <td className="num">
                    <QtyInput min={0} max={l.qty_ordered - l.qty_received} aria-label={`Quantity received of ${l.name}`}
                      value={Number(row(l).qty) || 0} onChange={(n) => setRecv({ ...recv, [l.id]: { ...row(l), qty: String(n) } })} />
                  </td>
                  <td className="num">
                    <input className="input-sm" value={row(l).cost} onChange={(e) => setRecv({ ...recv, [l.id]: { ...row(l), cost: e.target.value } })} />
                  </td>
                </>
              )}
            </tr>
          ))}
        </tbody>
      </table>
      {receiving && (
        <div className="form-grid">
          <Field label="Supplier invoice no." hint="Needed for the purchase book"><input value={invoice} onChange={(e) => setInvoice(e.target.value)} /></Field>
          <Field label="Supplier invoice date"><input type="date" value={invoiceDate} onChange={(e) => setInvoiceDate(e.target.value)} /></Field>
          <label className="check"><input type="checkbox" checked={isImport} onChange={(e) => setIsImport(e.target.checked)} /> Import (customs invoice)</label>
        </div>
      )}
      {po.receipts.length > 0 && (
        <>
          <h3>Goods receipts</h3>
          {po.receipts.map((r) => (
            <div key={r.id} className="list-row">
              <span>GR-{r.id} · {r.received_bs} BS · {r.by_name} {r.supplier_invoice_ref && `· inv ${r.supplier_invoice_ref} (${r.supplier_invoice_bs})`} · VAT {npr(r.vat_paisa)}{r.claimable_vat_paisa !== r.vat_paisa && ` (claimable ${npr(r.claimable_vat_paisa)})`}</span>
              <span className="small">{r.lines.map((l) => `${l.product} ×${l.qty} @ ${npr(l.unit_cost_paisa)}`).join(', ')}</span>
            </div>
          ))}
        </>
      )}
      <div className="modal-foot">
        {manage && po.status === 'draft' && <Button onClick={onEdit}>Edit</Button>}
        {manage && po.status === 'draft' && <Button kind="primary" onClick={() => void act('order')}>Mark ordered</Button>}
        {manage && ['draft', 'ordered'].includes(po.status) && <Button kind="danger" onClick={() => void act('cancel')}>Cancel PO</Button>}
        {receiving && (
          <Button kind="primary" onClick={() => {
            const lines = po.lines.map((l) => ({ po_line: l.id, qty: Number(row(l).qty), unit_cost_paisa: toPaisa(row(l).cost) })).filter((l) => l.qty > 0)
            const badCost = po.lines.find((l) => Number(row(l).qty) > 0 && toPaisa(row(l).cost) == null)
            if (badCost) return setError(`Invoice cost for ${badCost.name}: ${moneyProblem(row(badCost).cost) ?? 'enter the cost per unit'}`)
            if (!invoice.trim()) return setError('Enter the supplier invoice number (purchase book)')
            void act('receive', { lines, supplier_invoice_ref: invoice, supplier_invoice_date: invoiceDate, is_import: isImport })
          }}>Receive goods</Button>
        )}
      </div>
    </Modal>
  )
}

interface PReturn {
  id: number; number: string; issued_bs: string; supplier_name: string; reason: string; supplier_note_ref: string
  taxable_paisa: number; vat_paisa: number; lines: { product: string; sku: string; qty: number; unit_cost_paisa: number }[]
}

function PurchaseReturns({ manage }: { manage: boolean }) {
  const { branch } = useAuth()
  const { items, loading, error, reload, more } = usePaged<PReturn>(`purchase-returns/${qs({ b: branch?.id })}`)
  const [creating, setCreating] = useState(false)
  return (
    <>
      {manage && <div className="toolbar"><Button kind="primary" onClick={() => setCreating(true)}>Return goods to supplier</Button></div>}
      <ErrorBox error={error} />
      <div className="card flush">
        <table className="table">
          <thead><tr><th>Return number</th><th>Date (BS)</th><th>Supplier</th><th>Items</th><th className="num">Taxable</th><th className="num">VAT reversed</th><th>Reason</th></tr></thead>
          <tbody>
            {items.map((r) => (
              <tr key={r.id}>
                <td className="mono">{r.number}</td><td>{r.issued_bs}</td><td>{r.supplier_name}</td>
                <td className="small">{r.lines.map((l) => `${l.sku} ×${l.qty}`).join(', ')}</td>
                <td className="num">{npr(r.taxable_paisa)}</td><td className="num">{npr(r.vat_paisa)}</td><td>{r.reason}</td>
              </tr>
            ))}
            {!items.length && !loading && <tr><td colSpan={7} className="empty">No purchase returns</td></tr>}
          </tbody>
        </table>
        <LoadMore more={more} loading={loading} />
      </div>
      {creating && <PurchaseReturnForm onClose={() => setCreating(false)} onSaved={() => { setCreating(false); reload() }} />}
    </>
  )
}

function PurchaseReturnForm({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  const suppliers = useLoad(() => api<{ id: number; name: string }[]>('suppliers/'), [])
  const [supplier, setSupplier] = useState('')
  const [reason, setReason] = useState('')
  const [noteRef, setNoteRef] = useState('')
  const [lines, setLines] = useState<{ product: number; name: string; qty: number }[]>([])
  const [error, setError] = useState<string | null>(null)
  return (
    <Modal title="Return goods to supplier" onClose={onClose} wide>
      <ErrorBox error={error} />
      <p className="muted small">Stock leaves at its current average cost and the input VAT is reversed in the VAT return working.</p>
      <div className="form-grid">
        <Field label="Supplier">
          <select value={supplier} onChange={(e) => setSupplier(e.target.value)}>
            <option value="">Choose…</option>
            {suppliers.data?.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
        </Field>
        <Field label="Supplier's credit note no."><input value={noteRef} onChange={(e) => setNoteRef(e.target.value)} /></Field>
        <Field label="Reason"><input value={reason} onChange={(e) => setReason(e.target.value)} /></Field>
      </div>
      <ProductPicker onPick={(p) => setLines((ls) => (ls.some((l) => l.product === p.id) ? ls : [...ls, { product: p.id, name: p.name, qty: 1 }]))} />
      <table className="table">
        <tbody>
          {lines.map((l, i) => (
            <tr key={l.product}>
              <td>{l.name}</td>
              <td className="num"><QtyInput min={1} aria-label={`Quantity of ${l.name}`} value={l.qty} onChange={(n) => setLines(lines.map((x, j) => (j === i ? { ...x, qty: n } : x)))} /></td>
              <td><Button kind="ghost" onClick={() => setLines(lines.filter((_, j) => j !== i))}>Remove</Button></td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="modal-foot">
        <Button onClick={onClose}>Cancel</Button>
        <Button kind="primary" disabled={!supplier || !lines.length || !reason.trim()}
          why={!supplier ? 'Choose the supplier' : !lines.length ? 'Add the products going back' : 'Enter a reason for the return'} onClick={async () => {
          setError(null)
          try {
            await api('purchase-returns/', { body: { supplier: Number(supplier), reason, supplier_note_ref: noteRef, lines: lines.map((l) => ({ product: l.product, qty: l.qty })) } })
            onSaved()
          } catch (e) {
            setError((e as Error).message)
          }
        }}>Record return</Button>
      </div>
    </Modal>
  )
}

interface Supplier { id: number; name: string; pan: string; phone: string; email: string; address: string }

function Suppliers({ manage }: { manage: boolean }) {
  const { data, error, loading, reload } = useLoad(() => api<Supplier[]>('suppliers/'), [])
  const [q, setQ] = useState('')
  const [editing, setEditing] = useState<Supplier | 'new' | null>(null)
  const needle = q.trim().toLowerCase()
  const shown = (data ?? []).filter((s) => !needle || `${s.name} ${s.pan} ${s.phone} ${s.email}`.toLowerCase().includes(needle))
  return (
    <>
      <div className="toolbar">
        <span className="input-icon search">
          <Icon name="search" size={16} />
          <input placeholder="Search suppliers" value={q} onChange={(e) => setQ(e.target.value)} />
        </span>
        <span className="spacer" />
        {manage && <Button kind="primary" onClick={() => setEditing('new')}><Icon name="plus" size={16} /> Add supplier</Button>}
      </div>
      <ErrorBox error={error} />
      <div className="card flush">
        <table className="table">
          <thead><tr><th>Supplier</th><th>PAN / VAT</th><th>Contact number</th><th>Email</th><th>Address</th><th /></tr></thead>
          <tbody>
            {shown.map((s) => (
              <tr key={s.id}>
                <td><div className="cell-user"><span className="avatar avatar-sm">{initials(s.name)}</span><span className="cell-strong">{s.name}</span></div></td>
                <td className="mono">{s.pan || <span className="muted">—</span>}</td>
                <td>{s.phone || <span className="muted">—</span>}</td>
                <td>{s.email ? <a href={`mailto:${s.email}`}>{s.email}</a> : <span className="muted">—</span>}</td>
                <td className="details">{s.address || <span className="muted">—</span>}</td>
                <td className="row-actions">{manage && <Button kind="ghost" onClick={() => setEditing(s)}>Edit</Button>}</td>
              </tr>
            ))}
            {!shown.length && !loading && <tr><td colSpan={6} className="empty">{data?.length ? 'No suppliers match.' : 'No suppliers yet.'}</td></tr>}
          </tbody>
        </table>
      </div>
      {editing && <SupplierForm supplier={editing === 'new' ? null : editing} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); reload() }} />}
    </>
  )
}

function SupplierForm({ supplier, onClose, onSaved }: { supplier: Supplier | null; onClose: () => void; onSaved: () => void }) {
  const [f, setF] = useState({ name: supplier?.name ?? '', pan: supplier?.pan ?? '', phone: supplier?.phone ?? '', email: supplier?.email ?? '', address: supplier?.address ?? '' })
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const set = (k: keyof typeof f, v: string) => setF({ ...f, [k]: v })
  return (
    <Modal title={supplier ? `Edit ${supplier.name}` : 'New supplier'} onClose={onClose}>
      <ErrorBox error={error} />
      <Field label="Supplier name"><input autoFocus placeholder="Enter supplier name" value={f.name} onChange={(e) => set('name', e.target.value)} /></Field>
      <Field label="PAN / VAT number" hint="9 digits; needed for the purchase book"><input placeholder="123456789" maxLength={9} value={f.pan} onChange={(e) => set('pan', e.target.value.replace(/\D/g, ''))} /></Field>
      <div className="form-grid">
        <Field label="Contact number"><input placeholder="Enter contact number" value={f.phone} onChange={(e) => set('phone', e.target.value)} /></Field>
        <Field label="Email"><input type="email" placeholder="name@example.com" value={f.email} onChange={(e) => set('email', e.target.value)} /></Field>
      </div>
      <Field label="Address"><textarea rows={2} placeholder="Street, city" value={f.address} onChange={(e) => set('address', e.target.value)} /></Field>
      <div className="modal-foot">
        <Button onClick={onClose}>Discard</Button>
        <Button kind="primary" busy={busy} disabled={!f.name.trim()} onClick={async () => {
          setError(null)
          setBusy(true)
          try {
            await api(supplier ? `suppliers/${supplier.id}/` : 'suppliers/', { method: supplier ? 'PATCH' : 'POST', body: f })
            onSaved()
          } catch (e) {
            setError((e as Error).message)
          } finally {
            setBusy(false)
          }
        }}>{supplier ? 'Save' : 'Add supplier'}</Button>
      </div>
    </Modal>
  )
}
