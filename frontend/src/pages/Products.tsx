import { useState } from 'react'
import { useAuth } from '../auth'
import { Icon } from '../components/Icon'
import { Availability, ProductDetail } from '../components/ProductDetail'
import { Badge, Button, ErrorBox, Field, LoadMore, Modal, PageHead, Tabs, useLoad, usePaged } from '../components/ui'
import { api, qs } from '../lib/api'
import { npr, toPaisa, toRupeesInput } from '../lib/money'
import type { Barcode, Product } from '../lib/types'

interface Named { id: number; name: string }

export default function Products() {
  const { canOrg, can, branch } = useAuth()
  const [q, setQ] = useState('')
  const [view, setView] = useState<'active' | 'archived'>('active')
  const [editing, setEditing] = useState<Product | 'new' | null>(null)
  const [pricing, setPricing] = useState<Product | null>(null)
  const [viewing, setViewing] = useState<Product | null>(null)
  const { items, loading, error, reload, more } = usePaged<Product>(
    `products/${qs({ q, archived: view === 'archived' ? 1 : null, b: branch?.id })}`,
  )
  const manage = canOrg('catalog.manage')
  const showCost = manage || can('inventory.view')

  const archive = (p: Product) => void api(`products/${p.id}/${p.archived_at ? 'restore' : 'archive'}/`, { method: 'POST' }).then(reload)
  const actionsFor = (p: Product) => manage && (
    <>
      <Button kind="ghost" onClick={() => { setViewing(null); setEditing(p) }}>Edit</Button>
      <Button kind="ghost" onClick={() => { setViewing(null); setPricing(p) }}>Branch price</Button>
      <Button kind="ghost" onClick={() => { setViewing(null); archive(p) }}>{p.archived_at ? 'Restore' : 'Archive'}</Button>
    </>
  )

  return (
    <div>
      <PageHead title="Products" sub="Org-wide catalog: prices, tax and barcodes">
        <span className="input-icon search">
          <Icon name="search" size={16} />
          <input placeholder="Search name, SKU or barcode" value={q} onChange={(e) => setQ(e.target.value)} />
        </span>
        {manage && <Button kind="primary" onClick={() => setEditing('new')}><Icon name="plus" size={16} /> Add product</Button>}
      </PageHead>
      <Tabs value={view} onChange={setView} tabs={[['active', 'Active'], ['archived', 'Archived']]} />
      <ErrorBox error={error} />
      <div className="card flush">
        <table className="table">
          <thead>
            <tr>
              <th>Product</th><th>Category</th>
              {showCost && <th className="num">Cost (default)</th>}
              <th className="num">Price</th><th className="num">Branch price</th><th>Tax</th><th className="num">On hand</th><th>Availability</th><th />
            </tr>
          </thead>
          <tbody>
            {items.map((p) => (
              <tr key={p.id} className="clickable" onClick={() => setViewing(p)}>
                <td>
                  <div className="cell-user">
                    <span className="prod-thumb"><Icon name="package" size={18} /></span>
                    <div>
                      <div className="cell-strong">{p.name}</div>
                      <div className="mono muted small">{[p.sku, ...p.barcodes.map((b) => b.code + (b.pack_qty > 1 ? ` ×${b.pack_qty}` : ''))].join(' · ')}</div>
                    </div>
                  </div>
                </td>
                <td>{p.category_name ?? <span className="muted">—</span>}</td>
                {showCost && <td className="num">{npr(p.cost_price_paisa)}</td>}
                <td className="num cell-strong">{npr(p.sale_price_paisa)}</td>
                <td className="num">{p.branch_price_paisa != null ? npr(p.branch_price_paisa) : <span className="muted">—</span>}</td>
                <td>{p.tax_category_code ?? `${Number(p.tax_rate)}%`} {p.tax_inclusive ? <Badge>incl.</Badge> : <Badge tone="info">excl.</Badge>}</td>
                <td className={`num ${(p.on_hand ?? 0) < 0 ? 'text-bad' : ''}`}>{p.on_hand ?? '—'}</td>
                <td><Availability onHand={p.on_hand} reorderPoint={p.reorder_point ?? undefined} /></td>
                <td className="row-actions" onClick={(e) => e.stopPropagation()}>
                  {manage && <Button kind="ghost" onClick={() => setEditing(p)}>Edit</Button>}
                </td>
              </tr>
            ))}
            {!items.length && !loading && <tr><td colSpan={9} className="empty">No products.</td></tr>}
          </tbody>
        </table>
        <LoadMore more={more} loading={loading} />
      </div>
      {viewing && <ProductDetail productId={viewing.id} onClose={() => setViewing(null)} actions={actionsFor(viewing)} />}
      {editing && <ProductForm product={editing === 'new' ? null : editing} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); reload() }} />}
      {pricing && <BranchPriceForm product={pricing} onClose={() => setPricing(null)} onSaved={() => { setPricing(null); reload() }} />}
    </div>
  )
}

function ProductForm({ product, onClose, onSaved }: { product: Product | null; onClose: () => void; onSaved: () => void }) {
  const cats = useLoad(() => api<Named[]>('categories/'), [])
  const taxCats = useLoad(() => api<{ id: number; code: string; name: string; current_vat_rate: string | null }[]>('compliance/tax-categories/'), [])
  const sups = useLoad(() => api<Named[]>('suppliers/'), [])
  const [f, setF] = useState({
    sku: product?.sku ?? '', name: product?.name ?? '', category: product?.category ?? '', supplier: product?.supplier ?? '',
    unit: product?.unit ?? 'pcs', cost: toRupeesInput(product?.cost_price_paisa ?? 0), price: product ? toRupeesInput(product.sale_price_paisa) : '',
    tax_inclusive: product?.tax_inclusive ?? true, tax_category: product?.tax_category ?? '',
    hs_code: product?.hs_code ?? '', excise_rate: product?.excise_rate ?? '0', input_credit_blocked_pct: String(product?.input_credit_blocked_pct ?? 0),
  })
  const [barcodes, setBarcodes] = useState<Barcode[]>(product?.barcodes ?? [])
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const set = (k: keyof typeof f, v: string | boolean) => setF({ ...f, [k]: v })

  async function save() {
    const price = toPaisa(f.price)
    const cost = toPaisa(f.cost)
    if (price == null || cost == null) return setError('Prices must be numbers with up to 2 decimals')
    setBusy(true)
    try {
      const body = {
        sku: f.sku, name: f.name, category: f.category || null, supplier: f.supplier || null, unit: f.unit,
        cost_price_paisa: cost, sale_price_paisa: price, tax_inclusive: f.tax_inclusive,
        tax_category: f.tax_category || null, hs_code: f.hs_code, excise_rate: f.excise_rate || '0',
        input_credit_blocked_pct: Number(f.input_credit_blocked_pct) || 0,
        barcodes: barcodes.filter((b) => b.code.trim()),
      }
      await api(product ? `products/${product.id}/` : 'products/', { method: product ? 'PATCH' : 'POST', body })
      onSaved()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal title={product ? `Edit ${product.sku}` : 'New product'} onClose={onClose} wide>
      <ErrorBox error={error} />
      <h3 className="form-section">Primary details</h3>
      <div className="form-grid">
        <Field label="SKU"><input placeholder="Enter product SKU" value={f.sku} onChange={(e) => set('sku', e.target.value)} /></Field>
        <Field label="Name"><input placeholder="Enter product name" value={f.name} onChange={(e) => set('name', e.target.value)} /></Field>
        <Field label="Category">
          <select value={f.category} onChange={(e) => set('category', e.target.value)}>
            <option value="">—</option>
            {cats.data?.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
        </Field>
        <Field label="Supplier">
          <select value={f.supplier} onChange={(e) => set('supplier', e.target.value)}>
            <option value="">—</option>
            {sups.data?.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
        </Field>
        <Field label="Unit"><input placeholder="e.g. pcs, kg, packet" value={f.unit} onChange={(e) => set('unit', e.target.value)} /></Field>
      </div>
      <h3 className="form-section">Pricing &amp; tax</h3>
      <div className="form-grid">
        <Field label="Sale price (Rs)"><input inputMode="decimal" placeholder="Enter selling price" value={f.price} onChange={(e) => set('price', e.target.value)} /></Field>
        <Field label="Default cost (Rs)" hint="Purchase default only; real cost is the weighted average"><input inputMode="decimal" placeholder="Enter buying price" value={f.cost} onChange={(e) => set('cost', e.target.value)} /></Field>
        <Field label="Tax category" hint="Rates are effective-dated in Tax & IRD">
          <select value={f.tax_category} onChange={(e) => set('tax_category', e.target.value)}>
            <option value="">Choose…</option>
            {taxCats.data?.map((c) => <option key={c.id} value={c.id}>{c.code} — {c.name}{c.current_vat_rate != null ? ` (${Number(c.current_vat_rate)}%)` : ''}</option>)}
          </select>
        </Field>
        <Field label="HS code" hint="Printed on full tax invoices"><input value={f.hs_code} onChange={(e) => set('hs_code', e.target.value)} /></Field>
        <Field label="Excise (%)"><input value={f.excise_rate} onChange={(e) => set('excise_rate', e.target.value)} /></Field>
        <Field label="Blocked input credit" hint="Purchase VAT that can't be claimed">
          <select value={f.input_credit_blocked_pct} onChange={(e) => set('input_credit_blocked_pct', e.target.value)}>
            <option value="0">None</option>
            <option value="100">100% (petrol, alcohol/beverages, entertainment)</option>
            <option value="60">60% (vehicles)</option>
          </select>
        </Field>
        <label className="check"><input type="checkbox" checked={f.tax_inclusive} onChange={(e) => set('tax_inclusive', e.target.checked)} /> Price includes VAT</label>
      </div>
      <h3 className="form-section">Barcodes</h3>
      {barcodes.map((b, i) => (
        <div key={i} className="inline-form">
          <input placeholder="Code" value={b.code} onChange={(e) => setBarcodes(barcodes.map((x, j) => (j === i ? { ...x, code: e.target.value } : x)))} />
          <select value={b.type} onChange={(e) => setBarcodes(barcodes.map((x, j) => (j === i ? { ...x, type: e.target.value } : x)))}>
            {['ean13', 'ean8', 'upca', 'code128', 'other'].map((t) => <option key={t}>{t}</option>)}
          </select>
          <input className="input-sm" type="number" min={1} title="Units per scan (pack size)" value={b.pack_qty}
            onChange={(e) => setBarcodes(barcodes.map((x, j) => (j === i ? { ...x, pack_qty: Math.max(1, Number(e.target.value)) } : x)))} />
          <Button kind="ghost" onClick={() => setBarcodes(barcodes.filter((_, j) => j !== i))}>Remove</Button>
        </div>
      ))}
      <Button onClick={() => setBarcodes([...barcodes, { code: '', type: 'ean13', pack_qty: 1 }])}><Icon name="plus" size={16} /> Add barcode</Button>
      <div className="modal-foot">
        <Button onClick={onClose}>Cancel</Button>
        <Button kind="primary" busy={busy} onClick={() => void save()}>Save</Button>
      </div>
    </Modal>
  )
}

function BranchPriceForm({ product, onClose, onSaved }: { product: Product; onClose: () => void; onSaved: () => void }) {
  const { branch } = useAuth()
  const [raw, setRaw] = useState(product.branch_price_paisa != null ? toRupeesInput(product.branch_price_paisa) : '')
  const [error, setError] = useState<string | null>(null)
  const save = async (clear: boolean) => {
    const p = clear ? null : toPaisa(raw)
    if (!clear && p == null) return setError('Enter a valid price')
    try {
      await api(`products/${product.id}/branch-price/`, { body: { sale_price_paisa: p } })
      onSaved()
    } catch (e) {
      setError((e as Error).message)
    }
  }
  return (
    <Modal title={`${product.name} — price at ${branch?.code}`} onClose={onClose}>
      <ErrorBox error={error} />
      <p className="muted">Default price {npr(product.sale_price_paisa)}. Leave the override empty to use it.</p>
      <Field label="Branch price (Rs)"><input autoFocus value={raw} onChange={(e) => setRaw(e.target.value)} /></Field>
      <div className="modal-foot">
        <Button onClick={() => void save(true)}>Clear override</Button>
        <Button kind="primary" onClick={() => void save(false)}>Save</Button>
      </div>
    </Modal>
  )
}
