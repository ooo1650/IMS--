import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useAuth } from '../auth'
import { Badge, Button, ErrorBox, Field, fmtDate, Guide, LoadMore, Modal, MoneyInput, PageHead, Status, Tabs, useLoad, usePaged } from '../components/ui'
import { api, qs } from '../lib/api'
import { moneyProblem, npr, toPaisa } from '../lib/money'
import { printResult } from '../lib/printer'
import type { PosConfig, PrintResult, Sale } from '../lib/types'

type Filter = 'all' | 'unprinted' | 'conflicts' | 'review' | 'offline' | 'cancelled'

interface SaleRow {
  id: number; receipt_number: string; status: string; invoice_type: string; buyer_name: string; payment_method: string
  total_paisa: number; opened_at: string; paid_at: string | null; issued_bs: string; cashier_name: string
  register_code: string | null; offline: boolean; provisional_ref: string; stock_conflict: boolean
  conflict_resolution: string; needs_review: boolean; print_count: number
}

export default function Sales() {
  const [params, setParams] = useSearchParams()
  // ?filter=review / conflicts / … opens the list already filtered (links from "Needs attention").
  const [filter, setFilter] = useState<Filter>(() => {
    const f = params.get('filter')
    return (['unprinted', 'conflicts', 'review', 'offline', 'cancelled'] as string[]).includes(f ?? '') ? (f as Filter) : 'all'
  })
  const [q, setQ] = useState('')
  const { branch } = useAuth()
  const path = `sales/${qs({
    q, conflicts: filter === 'conflicts' ? 1 : null, review: filter === 'review' ? 1 : null,
    offline: filter === 'offline' ? 1 : null, cancelled: filter === 'cancelled' ? 1 : null,
    unprinted: filter === 'unprinted' ? 1 : null, b: branch?.id,
  })}`
  const { items, loading, error, reload, more } = usePaged<SaleRow>(path)
  const openId = params.get('id')

  return (
    <div>
      <PageHead title="Sales & invoices">
        <input className="search" placeholder="Invoice no., buyer or PAN…" value={q} onChange={(e) => setQ(e.target.value)} />
      </PageHead>
      <Guide id="sales" title="Every sale and invoice">
        <ul>
          <li>Search any bill to reprint it, return items or see how it was paid.</li>
          <li>Issued invoices can't be edited (tax rules). To correct one, make a <strong>return</strong>: it issues a credit note and puts the stock back.</li>
        </ul>
      </Guide>
      <Tabs value={filter} onChange={setFilter} tabs={[['all', 'All'], ['unprinted', 'Unprinted'], ['conflicts', 'Stock conflicts'],
        ['review', 'Needs review'], ['offline', 'Offline'], ['cancelled', 'Cancelled']]} />
      <ErrorBox error={error} />
      <div className="card flush">
        <table className="table">
          <thead>
            <tr><th>Invoice</th><th>Type</th><th>Status</th><th>Date (BS)</th><th>Buyer</th><th>Till</th><th>Paid by</th><th className="num">Total</th><th>Flags</th></tr>
          </thead>
          <tbody>
            {items.map((s) => (
              <tr key={s.id} className="clickable" onClick={() => setParams({ id: String(s.id) })}>
                <td className="mono">{s.receipt_number || <span className="muted">—</span>}</td>
                <td>{s.invoice_type === 'full' ? 'Tax invoice' : s.invoice_type ? 'Abbreviated' : ''}</td>
                <td><Status value={s.status === 'voided' && s.receipt_number ? 'cancelled' : s.status} /></td>
                <td>{s.issued_bs || fmtDate(s.paid_at ?? s.opened_at)}</td>
                <td>{s.buyer_name}</td>
                <td>{s.register_code}</td>
                <td>{s.payment_method}</td>
                <td className="num">{npr(s.total_paisa)}</td>
                <td className="flags">
                  {s.receipt_number && !s.print_count && <Badge tone="warn">not printed</Badge>}
                  {s.offline && <Badge>offline{s.provisional_ref ? ` · ${s.provisional_ref}` : ''}</Badge>}
                  {s.stock_conflict && !s.conflict_resolution && <Badge tone="bad">stock conflict</Badge>}
                  {s.conflict_resolution && <Badge tone="neutral">conflict {s.conflict_resolution}</Badge>}
                  {s.needs_review && <Badge tone="warn">review</Badge>}
                </td>
              </tr>
            ))}
            {!items.length && !loading && <tr><td colSpan={9} className="empty">No sales</td></tr>}
          </tbody>
        </table>
        <LoadMore more={more} loading={loading} />
      </div>
      {openId && <SaleDetail id={Number(openId)} onClose={() => { setParams({}); reload() }} />}
    </div>
  )
}

function SaleDetail({ id, onClose }: { id: number; onClose: () => void }) {
  const { can } = useAuth()
  const { data: sale, error, reload } = useLoad(() => api<Sale>(`sales/${id}/`), [id])
  const cfg = useLoad(() => api<PosConfig>('pos/config').catch(() => null), [])
  const [mode, setMode] = useState<null | 'cancel' | 'debit'>(null)
  const [reason, setReason] = useState('')
  const [amount, setAmount] = useState('')
  const [rate, setRate] = useState('')
  const [actionError, setActionError] = useState<string | null>(null)
  const register = cfg.data?.registers.find((r) => r.id === sale?.register) ?? undefined

  const act = async (fn: () => Promise<unknown>) => {
    setActionError(null)
    try {
      await fn()
      reload()
    } catch (e) {
      setActionError((e as Error).message)
    }
  }
  const print = (path: string, kind: 'original' | 'delivery' = 'original') =>
    act(async () => printResult(register, await api<PrintResult>(path, { body: { kind } })))

  const cancelled = sale?.status === 'voided' && !!sale.receipt_number
  return (
    <Modal title={sale ? `${sale.invoice_type === 'full' ? 'Tax invoice' : 'Invoice'} ${sale.receipt_number || `#${sale.id}`}` : 'Sale'} onClose={onClose} wide>
      <ErrorBox error={error || actionError} />
      {sale && (
        <>
          <div className="meta-row">
            <Status value={cancelled ? 'cancelled' : sale.status} />
            <span>Issued {sale.issued_bs} BS · {fmtDate(sale.paid_at ?? sale.opened_at)}</span>
            {sale.transaction_bs && sale.transaction_bs !== sale.issued_bs && <span>Transaction {sale.transaction_bs} BS</span>}
            <span>Till {sale.register_code}</span>
            <span>{sale.cashier_name}</span>
            <span>Paid by {sale.payment_method}</span>
            <Badge tone={sale.print_count ? 'neutral' : 'warn'}>{sale.print_count ? `printed ${sale.print_count}×` : 'not printed'}</Badge>
            {sale.offline && <Badge>offline{sale.provisional_ref ? ` · slip ${sale.provisional_ref}` : ''}</Badge>}
            {sale.cbms_status && <Badge tone={sale.cbms_status.status === 'synced' ? 'good' : sale.cbms_status.status === 'failed' ? 'bad' : 'warn'}>CBMS {sale.cbms_status.status}</Badge>}
            {sale.stock_conflict && <Badge tone="bad">stock conflict{sale.conflict_resolution ? ` · ${sale.conflict_resolution}` : ''}</Badge>}
            {sale.needs_review && <Badge tone="warn">needs review</Badge>}
          </div>
          {(sale.buyer_name || sale.buyer_pan) && (
            <div className="muted">Buyer: <strong>{sale.buyer_name}</strong>{sale.buyer_address && `, ${sale.buyer_address}`}{sale.buyer_pan && ` · PAN ${sale.buyer_pan}`}</div>
          )}
          {cancelled && <div className="alert alert-error">Cancelled: {sale.void_reason}</div>}
          <table className="table">
            <thead><tr><th>Item</th><th className="num">Qty</th><th className="num">Price</th><th className="num">Discount</th><th className="num">VAT</th><th className="num">Total</th><th className="num">Returned</th></tr></thead>
            <tbody>
              {sale.lines.map((l) => (
                <tr key={l.id}>
                  <td>{l.product_name} <span className="muted small">{l.sku}</span></td>
                  <td className="num">{l.qty}</td>
                  <td className="num">{npr(l.unit_price_paisa)}</td>
                  <td className="num">{l.line_discount_paisa ? `−${npr(l.line_discount_paisa)}` : ''}</td>
                  <td className="num">{npr(l.tax_amount_paisa)} <span className="muted small">{Number(l.tax_rate)}%</span></td>
                  <td className="num">{npr(l.line_total_paisa)}</td>
                  <td className="num">{l.returned_qty || ''}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="totals compact">
            <div className="total-row"><span>Subtotal (excl. tax)</span><span>{npr(sale.subtotal_paisa)}</span></div>
            <div className="total-row"><span>Discount</span><span>{sale.discount_total_paisa ? `−${npr(sale.discount_total_paisa)}` : npr(0)}</span></div>
            <div className="total-row"><span>VAT</span><span>{npr(sale.tax_total_paisa)}</span></div>
            {!!sale.excise_total_paisa && <div className="total-row"><span>Excise</span><span>{npr(sale.excise_total_paisa)}</span></div>}
            {!!sale.hst_total_paisa && <div className="total-row"><span>HST</span><span>{npr(sale.hst_total_paisa)}</span></div>}
            {!!sale.digital_vat_discount_paisa && <div className="total-row"><span>Digital VAT discount</span><span>−{npr(sale.digital_vat_discount_paisa)}</span></div>}
            {!!sale.round_adjust_paisa && <div className="total-row"><span>Rounding</span><span>{npr(sale.round_adjust_paisa)}</span></div>}
            <div className="grand"><span>Total</span><span>{npr(sale.total_paisa)}</span></div>
          </div>
          {(sale.credit_notes.length > 0 || sale.debit_notes.length > 0) && (
            <>
              <h3>Credit / debit notes</h3>
              {sale.credit_notes.map((c) => (
                <div key={c.id} className="list-row">
                  <span className="mono">{c.number}</span>
                  <span>{c.issued_bs} · −{npr(c.total_paisa)} <Button kind="ghost" onClick={() => void print(`credit-notes/${c.id}/print/`)}>Print</Button></span>
                </div>
              ))}
              {sale.debit_notes.map((d) => (
                <div key={d.id} className="list-row">
                  <span className="mono">{d.number}</span>
                  <span>{d.issued_bs} · +{npr(d.total_paisa)} <Button kind="ghost" onClick={() => void print(`debit-notes/${d.id}/print/`)}>Print</Button></span>
                </div>
              ))}
            </>
          )}
          <h3>Payments</h3>
          {sale.payments.map((p) => (
            <div key={p.id} className="list-row">
              <span>{p.method} <Status value={p.status} /> {p.ref && <span className="muted small">ref {p.ref}</span>}</span>
              <span>{npr(p.amount_paisa)}{p.change_paisa ? ` (tendered ${npr(p.tendered_paisa)})` : ''}</span>
            </div>
          ))}
          {mode === 'cancel' && (
            <Field label="Cancellation reason (printed on the cancelled invoice)">
              <input autoFocus value={reason} onChange={(e) => setReason(e.target.value)} />
            </Field>
          )}
          {mode === 'debit' && (
            <div className="form-grid">
              <Field label="Additional taxable amount (Rs, excl. VAT)" error={moneyProblem(amount)}><MoneyInput autoFocus value={amount} onChange={setAmount} /></Field>
              <Field label="VAT rate">
                <select value={rate} onChange={(e) => setRate(e.target.value)}>
                  <option value="">Choose…</option>
                  {[...new Set(['13.00', '0.00', ...sale.lines.map((l) => Number(l.tax_rate).toFixed(2))])].map((r) => (
                    <option key={r} value={r}>{Number(r)}%</option>
                  ))}
                </select>
              </Field>
              <Field label="Reason"><input value={reason} onChange={(e) => setReason(e.target.value)} /></Field>
            </div>
          )}
          <div className="modal-foot">
            {sale.receipt_number && (
              <>
                <Button onClick={() => void print(`sales/${sale.id}/print/`)}>{sale.print_count ? 'Reprint (copy)' : 'Print original'}</Button>
                {cfg.data && sale.total_paisa > cfg.data.delivery_copy_limit_paisa && (
                  <Button onClick={() => void print(`sales/${sale.id}/print/`, 'delivery')}>Delivery copy</Button>
                )}
                <a className="btn btn-default" href={`/api/v1/compliance/documents/invoice/${sale.id}?export=pdf`} target="_blank" rel="noreferrer">PDF (view)</a>
              </>
            )}
            {sale.stock_conflict && !sale.conflict_resolution && can('conflicts.resolve') && (
              <>
                <Button onClick={() => act(() => api(`sales/${sale.id}/resolve-conflict/`, { body: { resolution: 'accepted' } }))}>
                  Accept (item was there)
                </Button>
                <Button kind="danger" onClick={() => act(() => api(`sales/${sale.id}/resolve-conflict/`, { body: { resolution: 'refunded', reason: 'stock conflict refund' } }))}>
                  Refund &amp; cancel
                </Button>
              </>
            )}
            {sale.status === 'paid' && can('pos.void') && mode === null && (
              <>
                <Button onClick={() => { setMode('debit'); setReason(''); setRate('') }}>Debit note</Button>
                {!sale.credit_notes.length && !sale.debit_notes.length && (
                  <Button kind="danger" onClick={() => { setMode('cancel'); setReason('') }}>Cancel invoice</Button>
                )}
              </>
            )}
            {mode === 'cancel' && (
              <Button kind="danger" disabled={!reason.trim()} why="Enter the cancellation reason" onClick={() => act(async () => { await api(`sales/${sale.id}/void/`, { body: { reason } }); setMode(null) })}>
                Confirm cancellation
              </Button>
            )}
            {mode === 'debit' && (
              <Button kind="primary" disabled={!reason.trim() || !toPaisa(amount) || !rate}
                why={!toPaisa(amount) ? 'Enter the amount' : !rate ? 'Choose the VAT rate' : 'Enter a reason'} onClick={() => act(async () => {
                await api(`sales/${sale.id}/debit-note/`, { body: { taxable_paisa: toPaisa(amount), reason, tax_rate: rate } })
                setMode(null)
              })}>
                Issue debit note
              </Button>
            )}
            {mode && <Button onClick={() => setMode(null)}>Back</Button>}
          </div>
        </>
      )}
    </Modal>
  )
}
