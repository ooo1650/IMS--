import { useState } from 'react'
import { useAuth } from '../auth'
import { ProductPicker } from '../components/ProductPicker'
import { Button, ErrorBox, Field, fmtDate, Guide, LoadMore, Modal, PageHead, QtyInput, SkeletonRows, Status, useLoad, usePaged, ViewOnly } from '../components/ui'
import { api, qs } from '../lib/api'
import { npr } from '../lib/money'

interface TLine { id: number; product: number; sku: string; name: string; qty_sent: number; qty_received: number | null; unit_cost_paisa: number; variance_reason: string }
interface Transfer {
  id: number; from_branch: number; from_branch_code: string; to_branch: number; to_branch_code: string
  status: string; started_at: string; by_name: string; dispatched_at: string | null; received_at: string | null; note: string; lines: TLine[]
}

export default function Transfers() {
  const { branch, can } = useAuth()
  const { items, loading, error, reload, more } = usePaged<Transfer>(`transfers/${qs({ b: branch?.id })}`)
  const [creating, setCreating] = useState(false)
  const [open, setOpen] = useState<Transfer | null>(null)
  return (
    <div>
      <PageHead title="Stock transfers">
        {can('transfer.manage') && <Button kind="primary" onClick={() => setCreating(true)}>New transfer</Button>}
      </PageHead>
      {!can('transfer.manage') && <ViewOnly>Sending and receiving transfers needs a stock role at this branch. Ask your manager.</ViewOnly>}
      <Guide id="transfers" title="Moving stock between branches">
        <ol>
          <li><strong>New transfer</strong>: pick the branch it goes to and the items.</li>
          <li><strong>Dispatch</strong>: the stock leaves this branch.</li>
          <li>The other branch opens it and presses <strong>Receive</strong>: the stock arrives there. Any difference is recorded.</li>
        </ol>
      </Guide>
      <ErrorBox error={error} />
      <div className="card flush">
        <table className="table">
          <thead><tr><th>#</th><th>From → To</th><th>Status</th><th>Lines</th><th>Started</th><th>By</th></tr></thead>
          <tbody>
            {items.map((t) => (
              <tr key={t.id} className="clickable" onClick={() => setOpen(t)}>
                <td>{t.id}</td>
                <td><strong>{t.from_branch_code}</strong> → <strong>{t.to_branch_code}</strong> {t.to_branch === branch?.id && <span className="muted small">(incoming)</span>}</td>
                <td><Status value={t.status} /></td>
                <td>{t.lines.length}</td>
                <td>{fmtDate(t.started_at)}</td>
                <td>{t.by_name}</td>
              </tr>
            ))}
            {!items.length && loading && <SkeletonRows cols={6} />}
            {!items.length && !loading && <tr><td colSpan={6} className="empty">No transfers</td></tr>}
          </tbody>
        </table>
        <LoadMore more={more} loading={loading} />
      </div>
      {creating && <NewTransfer onClose={() => setCreating(false)} onSaved={() => { setCreating(false); reload() }} />}
      {open && <TransferDetail transfer={open} onClose={() => { setOpen(null); reload() }} />}
    </div>
  )
}

function NewTransfer({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  const { branch } = useAuth()
  const branches = useLoad(() => api<{ id: number; code: string; name: string }[]>('branches/directory/'), [])
  const [to, setTo] = useState('')
  const [note, setNote] = useState('')
  const [lines, setLines] = useState<{ product: number; name: string; qty: number }[]>([])
  const [error, setError] = useState<string | null>(null)
  return (
    <Modal title={`Transfer out of ${branch?.code}`} onClose={onClose} wide>
      <ErrorBox error={error} />
      <div className="form-grid">
        <Field label="To branch">
          <select value={to} onChange={(e) => setTo(e.target.value)}>
            <option value="">Choose…</option>
            {branches.data?.filter((b) => b.id !== branch?.id).map((b) => <option key={b.id} value={b.id}>{b.code} — {b.name}</option>)}
          </select>
        </Field>
        <Field label="Note"><input value={note} onChange={(e) => setNote(e.target.value)} /></Field>
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
        <Button kind="primary" disabled={!to || !lines.length} why={!to ? 'Choose the branch to send to' : 'Add the products to send'} onClick={async () => {
          setError(null)
          try {
            await api('transfers/', { body: { to_branch: Number(to), note, lines: lines.map((l) => ({ product: l.product, qty_sent: l.qty })) } })
            onSaved()
          } catch (e) {
            setError((e as Error).message)
          }
        }}>Save draft</Button>
      </div>
    </Modal>
  )
}

function TransferDetail({ transfer, onClose }: { transfer: Transfer; onClose: () => void }) {
  const { branch, can } = useAuth()
  const [t, setT] = useState(transfer)
  const [recv, setRecv] = useState<Record<number, { qty: number; reason: string }>>(
    Object.fromEntries(transfer.lines.map((l) => [l.id, { qty: l.qty_sent, reason: '' }])),
  )
  const [error, setError] = useState<string | null>(null)
  const isFrom = t.from_branch === branch?.id
  const isTo = t.to_branch === branch?.id
  const manage = can('transfer.manage')
  const act = async (path: string, body: unknown = {}) => {
    setError(null)
    try {
      setT(await api<Transfer>(`transfers/${t.id}/${path}/`, { body }))
    } catch (e) {
      setError((e as Error).message)
    }
  }
  const receiving = t.status === 'in_transit' && isTo && manage
  return (
    <Modal title={`Transfer #${t.id}: ${t.from_branch_code} → ${t.to_branch_code}`} onClose={onClose} wide>
      <ErrorBox error={error} />
      <div className="meta-row">
        <Status value={t.status} /> <span>Started {fmtDate(t.started_at)} by {t.by_name}</span>
        {t.dispatched_at && <span>Dispatched {fmtDate(t.dispatched_at)}</span>}
        {t.received_at && <span>Received {fmtDate(t.received_at)}</span>}
      </div>
      {t.note && <p>{t.note}</p>}
      <table className="table">
        <thead><tr><th>Product</th><th className="num">Sent</th><th className="num">Unit cost</th><th className="num">Received</th><th>Variance reason</th></tr></thead>
        <tbody>
          {t.lines.map((l) => (
            <tr key={l.id}>
              <td>{l.name} <span className="muted small">{l.sku}</span></td>
              <td className="num">{l.qty_sent}</td>
              <td className="num">{l.unit_cost_paisa ? npr(l.unit_cost_paisa) : '—'}</td>
              <td className="num">
                {receiving ? (
                  <QtyInput min={0} max={l.qty_sent} aria-label={`Quantity received of ${l.name}`} value={recv[l.id].qty}
                    onChange={(n) => setRecv({ ...recv, [l.id]: { ...recv[l.id], qty: n } })} />
                ) : (l.qty_received ?? '—')}
              </td>
              <td>
                {receiving && recv[l.id].qty < l.qty_sent ? (
                  <input placeholder="Required for short receipt" value={recv[l.id].reason} onChange={(e) => setRecv({ ...recv, [l.id]: { ...recv[l.id], reason: e.target.value } })} />
                ) : l.variance_reason}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="modal-foot">
        {manage && isFrom && t.status === 'draft' && <Button kind="primary" onClick={() => void act('dispatch')}>Dispatch (deduct stock)</Button>}
        {manage && isFrom && ['draft', 'in_transit'].includes(t.status) && <Button kind="danger" onClick={() => void act('cancel')}>Cancel transfer</Button>}
        {receiving && (
          <Button kind="primary" onClick={() => void act('receive', { lines: t.lines.map((l) => ({ id: l.id, qty_received: recv[l.id].qty, variance_reason: recv[l.id].reason })) })}>
            Receive
          </Button>
        )}
      </div>
    </Modal>
  )
}
