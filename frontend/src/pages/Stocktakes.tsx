import { useEffect, useState } from 'react'
import { useAuth } from '../auth'
import { Button, ErrorBox, Field, LoadMore, Modal, PageHead, Status, fmtDate, useLoad, usePaged, Guide } from '../components/ui'
import { api, qs } from '../lib/api'

interface Stocktake { id: number; status: string; started_at: string; finished_at: string | null; by_name: string; scope: { category_name?: string }; line_count: number; counted_count: number }
interface Line { id: number; product: number; sku: string; name: string; system_qty: number; counted_qty: number | null; variance: number | null; reason: string }

export default function Stocktakes() {
  const { branch } = useAuth()
  const { items, loading, error, reload, more } = usePaged<Stocktake>(`stocktakes/${qs({ b: branch?.id })}`)
  const [starting, setStarting] = useState(false)
  const [open, setOpen] = useState<Stocktake | null>(null)
  return (
    <div>
      <PageHead title="Stocktakes">
        <Button kind="primary" onClick={() => setStarting(true)}>Start stocktake</Button>
      </PageHead>
      <Guide id="stocktakes" title="Counting what's on the shelf">
        <ol>
          <li><strong>Start stocktake</strong> for the whole branch or a category.</li>
          <li>Enter what you actually count and <strong>Save counts</strong> as you go.</li>
          <li><strong>Finish &amp; post adjustments</strong>: differences become logged stock adjustments.</li>
        </ol>
      </Guide>
      <ErrorBox error={error} />
      <div className="card flush">
        <table className="table">
          <thead><tr><th>#</th><th>Scope</th><th>Status</th><th>Counted</th><th>Started</th><th>Finished</th><th>By</th></tr></thead>
          <tbody>
            {items.map((s) => (
              <tr key={s.id} className="clickable" onClick={() => setOpen(s)}>
                <td>{s.id}</td>
                <td>{s.scope.category_name ?? 'Full'}</td>
                <td><Status value={s.status} /></td>
                <td>{s.counted_count} / {s.line_count}</td>
                <td>{fmtDate(s.started_at)}</td>
                <td>{fmtDate(s.finished_at)}</td>
                <td>{s.by_name}</td>
              </tr>
            ))}
            {!items.length && !loading && <tr><td colSpan={7} className="empty">No stocktakes yet</td></tr>}
          </tbody>
        </table>
        <LoadMore more={more} loading={loading} />
      </div>
      {starting && <StartModal onClose={() => setStarting(false)} onStarted={(s) => { setStarting(false); reload(); setOpen(s) }} />}
      {open && <CountModal stocktake={open} onClose={() => { setOpen(null); reload() }} />}
    </div>
  )
}

function StartModal({ onClose, onStarted }: { onClose: () => void; onStarted: (s: Stocktake) => void }) {
  const cats = useLoad(() => api<{ id: number; name: string }[]>('categories/'), [])
  const [category, setCategory] = useState('')
  const [error, setError] = useState<string | null>(null)
  return (
    <Modal title="Start stocktake" onClose={onClose}>
      <ErrorBox error={error} />
      <p className="muted">System quantities are snapshotted now. Variances are measured against that snapshot.</p>
      <Field label="Scope">
        <select value={category} onChange={(e) => setCategory(e.target.value)}>
          <option value="">Full branch</option>
          {cats.data?.map((c) => <option key={c.id} value={c.id}>Category: {c.name}</option>)}
        </select>
      </Field>
      <div className="modal-foot">
        <Button onClick={onClose}>Cancel</Button>
        <Button kind="primary" onClick={async () => {
          try {
            onStarted(await api<Stocktake>('stocktakes/', { body: { category: category || null } }))
          } catch (e) {
            setError((e as Error).message)
          }
        }}>Start</Button>
      </div>
    </Modal>
  )
}

function CountModal({ stocktake, onClose }: { stocktake: Stocktake; onClose: () => void }) {
  const [lines, setLines] = useState<Line[]>([])
  const [dirty, setDirty] = useState<Record<number, { counted_qty: string; reason: string }>>({})
  const [q, setQ] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [status, setStatus] = useState(stocktake.status)
  const counting = status === 'counting'
  const [confirmUncounted, setConfirmUncounted] = useState<number | null>(null)

  useEffect(() => {
    void api<Line[]>(`stocktakes/${stocktake.id}/lines/${qs({ q })}`).then(setLines).catch((e) => setError(e.message))
  }, [stocktake.id, q])

  const save = async () => {
    const updates = Object.entries(dirty).map(([id, v]) => ({ id: Number(id), counted_qty: v.counted_qty === '' ? null : Number(v.counted_qty), reason: v.reason }))
    if (!updates.length) return
    setLines(await api<Line[]>(`stocktakes/${stocktake.id}/lines/${qs({ q })}`, { body: { lines: updates } }))
    setDirty({})
  }

  return (
    <Modal title={`Stocktake #${stocktake.id} · ${stocktake.scope.category_name ?? 'full'}`} onClose={onClose} wide>
      <ErrorBox error={error} />
      <div className="inline-form">
        <input placeholder="Filter products…" value={q} onChange={(e) => setQ(e.target.value)} />
        <Status value={status} />
      </div>
      <div className="table-scroll tall">
        <table className="table">
          <thead><tr><th>Product</th><th className="num">System</th><th className="num">Counted</th><th className="num">Variance</th><th>Reason</th></tr></thead>
          <tbody>
            {lines.map((l) => {
              const d = dirty[l.id]
              const counted = d ? d.counted_qty : l.counted_qty == null ? '' : String(l.counted_qty)
              const variance = counted === '' ? null : Number(counted) - l.system_qty
              return (
                <tr key={l.id}>
                  <td>{l.name} <span className="muted small">{l.sku}</span></td>
                  <td className="num">{l.system_qty}</td>
                  <td className="num">
                    {counting ? (
                      <input className="input-sm" type="number" value={counted}
                        onChange={(e) => setDirty({ ...dirty, [l.id]: { counted_qty: e.target.value, reason: d?.reason ?? l.reason } })} />
                    ) : (l.counted_qty ?? '—')}
                  </td>
                  <td className={`num ${variance ? 'text-bad' : ''}`}>{variance ?? ''}</td>
                  <td>
                    {/* Always rendered while counting so rows never shift under the counter's cursor. */}
                    {counting ? (
                      <input value={d?.reason ?? l.reason} placeholder={variance ? 'Reason' : ''} disabled={!variance}
                        onChange={(e) => setDirty({ ...dirty, [l.id]: { counted_qty: counted, reason: e.target.value } })} />
                    ) : l.reason}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      {counting && (
        <div className="modal-foot">
          <Button disabled={!Object.keys(dirty).length} onClick={() => void save().catch((e) => setError(e.message))}>Save counts</Button>
          <Button kind="primary" onClick={async () => {
            setError(null)
            try {
              await save()
              // Uncounted lines are left unchanged; make that an explicit second click.
              const all = await api<Line[]>(`stocktakes/${stocktake.id}/lines/`)
              const uncounted = all.filter((l) => l.counted_qty == null).length
              if (uncounted && confirmUncounted !== uncounted) {
                setConfirmUncounted(uncounted)
                return
              }
              await api(`stocktakes/${stocktake.id}/finish/`, { method: 'POST' })
              setStatus('finished')
            } catch (e) {
              setError((e as Error).message)
            }
          }}>
            {confirmUncounted ? `Finish anyway — ${confirmUncounted} uncounted left unchanged` : 'Finish & post adjustments'}
          </Button>
        </div>
      )}
    </Modal>
  )
}
