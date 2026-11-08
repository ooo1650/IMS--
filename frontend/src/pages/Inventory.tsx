import { useState } from 'react'
import { useAuth } from '../auth'
import { Icon } from '../components/Icon'
import { Availability, ProductDetail } from '../components/ProductDetail'
import { Badge, Button, ErrorBox, Field, LoadMore, Modal, PageHead, Tabs, fmtDate, useLoad, usePaged } from '../components/ui'
import { api, qs } from '../lib/api'
import { npr } from '../lib/money'
import { Tile } from './Dashboard'

interface Item {
  id: number; product: number; sku: string; name: string; unit: string; category: string | null
  quantity_on_hand: number; reorder_point: number; reorder_qty: number; weighted_avg_cost_paisa: number
  allow_negative: boolean; value_paisa: number
}
interface Movement {
  id: number; sku: string; name: string; type: string; qty: number; unit_cost_paisa: number; ref: string
  ref_type: string; at: string; by_name: string | null; reason: string
}

interface Summary { items: number; inventory_value_paisa: number; low_stock: number; out_of_stock: number; negative_stock: number }
type Filter = 'all' | 'low' | 'out' | 'negative'

export default function Inventory() {
  const { can, branch } = useAuth()
  const [tab, setTab] = useState<'stock' | 'movements'>('stock')
  const [filter, setFilter] = useState<Filter>('all')
  const [q, setQ] = useState('')
  const [adjusting, setAdjusting] = useState<Item | null>(null)
  const [settings, setSettings] = useState<Item | null>(null)
  const [viewing, setViewing] = useState<Item | null>(null)
  const summary = useLoad(() => api<Summary>(`inventory/summary/${qs({ b: branch?.id })}`), [branch?.id])
  const stock = usePaged<Item>(tab === 'movements' ? null : `inventory/${qs({ q, low: filter === 'low' ? 1 : null, out: filter === 'out' ? 1 : null, negative: filter === 'negative' ? 1 : null, b: branch?.id })}`)
  const moves = usePaged<Movement>(tab === 'movements' ? `movements/${qs({ b: branch?.id })}` : null)
  const canAdjust = can('inventory.adjust')
  const s = summary.data
  const refresh = () => { stock.reload(); summary.reload() }

  return (
    <div>
      <PageHead title={`Inventory · ${branch?.code}`} sub="Stock on hand, reorder levels and movements at this branch">
        {tab === 'stock' && (
          <span className="input-icon search">
            <Icon name="search" size={16} />
            <input placeholder="Search product or SKU" value={q} onChange={(e) => setQ(e.target.value)} />
          </span>
        )}
      </PageHead>
      <div className="tiles">
        <Tile label="Products tracked" value={s?.items ?? '—'} />
        <Tile label="Stock value" value={s ? npr(s.inventory_value_paisa) : '—'} sub="on hand × weighted-avg cost" />
        <Tile label="Low stock" value={s?.low_stock ?? '—'} tone={s?.low_stock ? 'warn' : undefined} sub="at or below reorder point" />
        <Tile label="Out of stock" value={s?.out_of_stock ?? '—'} tone={s?.out_of_stock ? 'bad' : undefined}
          sub={s?.negative_stock ? `${s.negative_stock} below zero — recount` : 'nothing on hand'} />
      </div>
      <Tabs value={tab} onChange={setTab} tabs={[['stock', 'Stock on hand'], ['movements', 'Movements']]} />
      <ErrorBox error={stock.error || moves.error || summary.error} />
      {tab === 'stock' ? (
        <>
          <div className="toolbar">
            <div className="chips">
              {([['all', 'All stock'], ['low', 'Low stock'], ['out', 'Out of stock'], ['negative', 'Negative (recount)']] as [Filter, string][]).map(([k, label]) => (
                <button key={k} className={`chip ${filter === k ? 'active' : ''}`} onClick={() => setFilter(k)}>{label}</button>
              ))}
            </div>
          </div>
          <div className="card flush">
            <table className="table">
              <thead>
                <tr><th>Product</th><th>Category</th><th className="num">On hand</th><th className="num">Reorder at</th><th className="num">Avg cost</th><th className="num">Value</th><th>Availability</th><th /></tr>
              </thead>
              <tbody>
                {stock.items.map((i) => (
                  <tr key={i.id} className="clickable" onClick={() => setViewing(i)}>
                    <td>
                      <div className="cell-user">
                        <span className="prod-thumb"><Icon name="package" size={18} /></span>
                        <div>
                          <div className="cell-strong">{i.name}</div>
                          <div className="mono muted small">{i.sku}{i.allow_negative && <> · <Badge>neg. allowed</Badge></>}</div>
                        </div>
                      </div>
                    </td>
                    <td>{i.category ?? <span className="muted">—</span>}</td>
                    <td className={`num ${i.quantity_on_hand < 0 ? 'text-bad' : i.quantity_on_hand <= i.reorder_point ? 'text-warn' : ''}`}>
                      {i.quantity_on_hand} <span className="muted small">{i.unit}</span>
                    </td>
                    <td className="num">{i.reorder_point}</td>
                    <td className="num">{npr(i.weighted_avg_cost_paisa)}</td>
                    <td className="num">{npr(i.value_paisa)}</td>
                    <td><Availability onHand={i.quantity_on_hand} reorderPoint={i.reorder_point} /></td>
                    <td className="row-actions" onClick={(e) => e.stopPropagation()}>
                      {canAdjust && <Button kind="ghost" onClick={() => setAdjusting(i)}>Adjust</Button>}
                      {canAdjust && <button className="icon-btn" title="Stock settings" aria-label="Stock settings" onClick={() => setSettings(i)}><Icon name="settings" size={16} /></button>}
                    </td>
                  </tr>
                ))}
                {!stock.items.length && !stock.loading && <tr><td colSpan={8} className="empty">Nothing here</td></tr>}
              </tbody>
            </table>
            <LoadMore more={stock.more} loading={stock.loading} />
          </div>
        </>
      ) : (
        <div className="card flush">
          <table className="table">
            <thead><tr><th>When</th><th>Product</th><th>Type</th><th className="num">Qty</th><th className="num">Unit cost</th><th>Ref</th><th>By</th><th>Reason</th></tr></thead>
            <tbody>
              {moves.items.map((m) => (
                <tr key={m.id}>
                  <td>{fmtDate(m.at)}</td>
                  <td><span className="cell-strong">{m.name}</span> <span className="mono muted small">{m.sku}</span></td>
                  <td><Badge>{m.type.replace(/_/g, ' ')}</Badge></td>
                  <td className={`num ${m.qty < 0 ? 'text-bad' : 'text-good'}`}>{m.qty > 0 ? `+${m.qty}` : m.qty}</td>
                  <td className="num">{npr(m.unit_cost_paisa)}</td>
                  <td className="muted small">{m.ref_type} {m.ref}</td>
                  <td>{m.by_name}</td>
                  <td className="small">{m.reason}</td>
                </tr>
              ))}
              {!moves.items.length && !moves.loading && <tr><td colSpan={8} className="empty">No movements yet</td></tr>}
            </tbody>
          </table>
          <LoadMore more={moves.more} loading={moves.loading} />
        </div>
      )}
      {viewing && (
        <ProductDetail
          productId={viewing.product}
          stock={viewing}
          onClose={() => setViewing(null)}
          actions={canAdjust && (
            <>
              <Button onClick={() => { setAdjusting(viewing); setViewing(null) }}>Adjust stock</Button>
              <Button kind="ghost" onClick={() => { setSettings(viewing); setViewing(null) }}><Icon name="settings" size={16} /> Settings</Button>
            </>
          )}
        />
      )}
      {adjusting && <AdjustModal item={adjusting} onClose={() => setAdjusting(null)} onDone={() => { setAdjusting(null); refresh() }} />}
      {settings && <SettingsModal item={settings} onClose={() => setSettings(null)} onDone={() => { setSettings(null); refresh() }} />}
    </div>
  )
}

function AdjustModal({ item, onClose, onDone }: { item: Item; onClose: () => void; onDone: () => void }) {
  const [dir, setDir] = useState<'in' | 'out'>('out')
  const [qty, setQty] = useState('1')
  const [reason, setReason] = useState('')
  const [error, setError] = useState<string | null>(null)
  const n = parseInt(qty, 10)
  return (
    <Modal title={`Adjust ${item.name}`} onClose={onClose}>
      <ErrorBox error={error} />
      <p className="muted">On hand: {item.quantity_on_hand}. Adjustments are logged as stock movements at the current average cost.</p>
      <div className="form-grid">
        <Field label="Direction">
          <select value={dir} onChange={(e) => setDir(e.target.value as 'in' | 'out')}>
            <option value="out">Remove (damage, loss, expiry)</option>
            <option value="in">Add (found, correction)</option>
          </select>
        </Field>
        <Field label="Quantity"><input type="number" min={1} value={qty} onChange={(e) => setQty(e.target.value)} /></Field>
      </div>
      <Field label="Reason"><input value={reason} onChange={(e) => setReason(e.target.value)} /></Field>
      <div className="modal-foot">
        <Button onClick={onClose}>Cancel</Button>
        <Button
          kind="primary"
          disabled={!(n > 0) || !reason.trim()}
          onClick={async () => {
            try {
              await api('inventory/adjust/', { body: { product: item.product, qty: dir === 'in' ? n : -n, reason } })
              onDone()
            } catch (e) {
              setError((e as Error).message)
            }
          }}
        >
          Save adjustment
        </Button>
      </div>
    </Modal>
  )
}

function SettingsModal({ item, onClose, onDone }: { item: Item; onClose: () => void; onDone: () => void }) {
  const [f, setF] = useState({ reorder_point: item.reorder_point, reorder_qty: item.reorder_qty, allow_negative: item.allow_negative })
  const [error, setError] = useState<string | null>(null)
  return (
    <Modal title={`${item.name} — stock settings`} onClose={onClose}>
      <ErrorBox error={error} />
      <div className="form-grid">
        <Field label="Reorder point"><input type="number" min={0} value={f.reorder_point} onChange={(e) => setF({ ...f, reorder_point: Number(e.target.value) })} /></Field>
        <Field label="Reorder quantity"><input type="number" min={0} value={f.reorder_qty} onChange={(e) => setF({ ...f, reorder_qty: Number(e.target.value) })} /></Field>
      </div>
      <label className="check">
        <input type="checkbox" checked={f.allow_negative} onChange={(e) => setF({ ...f, allow_negative: e.target.checked })} />
        Allow selling below zero (for fast movers whose count lags; flagged for recount)
      </label>
      <div className="modal-foot">
        <Button onClick={onClose}>Cancel</Button>
        <Button kind="primary" onClick={async () => {
          try {
            await api(`inventory/${item.id}/`, { method: 'PATCH', body: f })
            onDone()
          } catch (e) {
            setError((e as Error).message)
          }
        }}>Save</Button>
      </div>
    </Modal>
  )
}
