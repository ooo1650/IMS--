/** Product overview + stock history for the active branch (Products and Inventory share it). */
import { useState, type ReactNode } from 'react'
import { useAuth } from '../auth'
import { api, qs } from '../lib/api'
import { npr } from '../lib/money'
import type { Product } from '../lib/types'
import { ProductThumb } from './ProductThumb'
import { Badge, ErrorBox, LoadMore, Modal, Tabs, fmtDate, useLoad, usePaged } from './ui'

interface Supplier { id: number; name: string; pan: string; phone: string; email: string; address: string }
interface Movement { id: number; type: string; qty: number; unit_cost_paisa: number; ref: string; ref_type: string; at: string; by_name: string | null; reason: string }
export interface StockInfo { quantity_on_hand: number; reorder_point: number; reorder_qty: number; weighted_avg_cost_paisa: number; value_paisa: number }

export function Availability({ onHand, reorderPoint }: { onHand: number | null | undefined; reorderPoint?: number }) {
  if (onHand == null) return <span className="muted">—</span>
  if (onHand <= 0) return <span className="avail avail-out">Out of stock</span>
  if (reorderPoint != null && reorderPoint > 0 && onHand <= reorderPoint) return <span className="avail avail-low">Low stock</span>
  return <span className="avail avail-in">In stock</span>
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <>
      <dt>{label}</dt>
      <dd>{children}</dd>
    </>
  )
}

export function ProductDetail({ productId, stock, actions, onClose }: {
  productId: number
  stock?: StockInfo
  actions?: ReactNode
  onClose: () => void
}) {
  const { can, canOrg, branch } = useAuth()
  const [tab, setTab] = useState<'overview' | 'history'>('overview')
  const { data: p, error } = useLoad(() => api<Product>(`products/${productId}/`), [productId])
  const supplier = useLoad(() => (p?.supplier ? api<Supplier>(`suppliers/${p.supplier}/`) : Promise.resolve(null)), [p?.supplier])
  const canStock = can('inventory.view')
  const moves = usePaged<Movement>(tab === 'history' && canStock ? `movements/${qs({ product: productId, b: branch?.id })}` : null)
  const showCost = canOrg('catalog.manage') || canStock
  const onHand = stock?.quantity_on_hand ?? p?.on_hand ?? null

  return (
    <Modal title={p ? p.name : 'Product'} onClose={onClose} wide>
      <ErrorBox error={error} />
      {!p ? <div className="splash">Loading…</div> : (
        <>
          <div className="detail-head">
            <ProductThumb src={p.image_url} large />
            <div>
              <div className="mono muted">{p.sku}</div>
              <div className="flags">
                <Availability onHand={onHand} reorderPoint={stock?.reorder_point ?? p.reorder_point ?? undefined} />
                {p.archived_at && <Badge tone="bad">archived</Badge>}
              </div>
            </div>
            <div className="page-actions">{actions}</div>
          </div>
          <Tabs value={tab} onChange={setTab} tabs={canStock ? [['overview', 'Overview'], ['history', 'Stock history']] : [['overview', 'Overview']]} />
          {tab === 'overview' ? (
            <div className="detail-grid">
              <div>
                <h3>Primary details</h3>
                <dl className="dl">
                  <Row label="Product name">{p.name}</Row>
                  <Row label="SKU"><span className="mono">{p.sku}</span></Row>
                  <Row label="Category">{p.category_name ?? '—'}</Row>
                  <Row label="Unit">{p.unit}</Row>
                  <Row label="Barcodes">{p.barcodes.length ? p.barcodes.map((b) => b.code + (b.pack_qty > 1 ? ` ×${b.pack_qty}` : '')).join(', ') : '—'}</Row>
                </dl>
                <h3>Pricing &amp; tax</h3>
                <dl className="dl">
                  <Row label="Sale price">{npr(p.sale_price_paisa)}</Row>
                  {p.branch_price_paisa != null && <Row label={`Price at ${branch?.code}`}>{npr(p.branch_price_paisa)}</Row>}
                  {showCost && p.cost_price_paisa != null && <Row label="Default cost">{npr(p.cost_price_paisa)}</Row>}
                  <Row label="VAT">{p.tax_category_code ?? `${Number(p.tax_rate)}%`} {p.tax_inclusive ? '(included)' : '(added)'}</Row>
                  {p.hs_code && <Row label="HS code">{p.hs_code}</Row>}
                  {Number(p.excise_rate) > 0 && <Row label="Excise">{Number(p.excise_rate)}%</Row>}
                </dl>
                <h3>Supplier</h3>
                {supplier.data ? (
                  <dl className="dl">
                    <Row label="Name">{supplier.data.name}</Row>
                    {supplier.data.pan && <Row label="PAN">{supplier.data.pan}</Row>}
                    {supplier.data.phone && <Row label="Contact">{supplier.data.phone}</Row>}
                    {supplier.data.email && <Row label="Email">{supplier.data.email}</Row>}
                  </dl>
                ) : <p className="muted">{p.supplier_name ?? 'No default supplier'}</p>}
              </div>
              <aside className="stock-panel">
                <div className="stock-stat"><span>On hand at {branch?.code}</span><strong className={onHand != null && onHand < 0 ? 'text-bad' : ''}>{onHand ?? '—'} <small>{p.unit}</small></strong></div>
                {stock && (
                  <>
                    <div className="stock-stat"><span>Reorder point</span><strong>{stock.reorder_point}</strong></div>
                    <div className="stock-stat"><span>Reorder quantity</span><strong>{stock.reorder_qty}</strong></div>
                    {showCost && <div className="stock-stat"><span>Average cost</span><strong>{npr(stock.weighted_avg_cost_paisa)}</strong></div>}
                    {showCost && <div className="stock-stat"><span>Stock value</span><strong>{npr(stock.value_paisa)}</strong></div>}
                  </>
                )}
              </aside>
            </div>
          ) : (
            <div className="table-scroll tall">
              <ErrorBox error={moves.error} />
              <table className="table">
                <thead><tr><th>When</th><th>Type</th><th className="num">Qty</th><th>Ref</th><th>By</th><th>Reason</th></tr></thead>
                <tbody>
                  {moves.items.map((m) => (
                    <tr key={m.id}>
                      <td>{fmtDate(m.at)}</td>
                      <td><Badge>{m.type.replace(/_/g, ' ')}</Badge></td>
                      <td className={`num ${m.qty < 0 ? 'text-bad' : 'text-good'}`}>{m.qty > 0 ? `+${m.qty}` : m.qty}</td>
                      <td className="muted small">{m.ref_type} {m.ref}</td>
                      <td>{m.by_name}</td>
                      <td className="small">{m.reason}</td>
                    </tr>
                  ))}
                  {!moves.items.length && !moves.loading && <tr><td colSpan={6} className="empty">No stock movements at this branch</td></tr>}
                </tbody>
              </table>
              <LoadMore more={moves.more} loading={moves.loading} />
            </div>
          )}
        </>
      )}
    </Modal>
  )
}
