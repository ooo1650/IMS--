import { useEffect, useMemo, useRef, useState } from 'react'
import { useAuth } from '../auth'
import { CameraScanner } from '../components/CameraScanner'
import { Icon } from '../components/Icon'
import { LabelPrint } from '../components/LabelPrint'
import { Availability, ProductDetail } from '../components/ProductDetail'
import { ProductThumb } from '../components/ProductThumb'
import { Badge, Button, ErrorBox, Field, Guide, LoadMore, Modal, MoneyInput, PageHead, QtyInput, Tabs, useLoad, usePaged } from '../components/ui'
import { api, qs } from '../lib/api'
import { generateInStoreEan13, isValidEan13 } from '../lib/barcode'
import { downscaleImage } from '../lib/images'
import { moneyProblem, normalizeNumber, npr, parseWhole, toPaisa, toRupeesInput, wholeProblem } from '../lib/money'
import type { Barcode, Product } from '../lib/types'

interface Named { id: number; name: string }

export default function Products() {
  const { canOrg, can, branch } = useAuth()
  const [q, setQ] = useState('')
  const [view, setView] = useState<'active' | 'archived'>('active')
  const [editing, setEditing] = useState<Product | 'new' | null>(null)
  const [formKey, setFormKey] = useState(0) // a new key gives "Save & add another" a blank form
  const [pricing, setPricing] = useState<Product | null>(null)
  const [viewing, setViewing] = useState<Product | null>(null)
  const [selected, setSelected] = useState<Map<number, Product>>(new Map())
  const [labels, setLabels] = useState<Product[] | null>(null)
  const toggle = (p: Product) => setSelected((m) => {
    const n = new Map(m)
    if (n.has(p.id)) n.delete(p.id)
    else n.set(p.id, p)
    return n
  })
  const { items, loading, error, reload, more } = usePaged<Product>(
    `products/${qs({ q, archived: view === 'archived' ? 1 : null, b: branch?.id })}`,
  )
  const allShown = items.length > 0 && items.every((p) => selected.has(p.id))
  const manage = canOrg('catalog.manage')
  const showCost = manage || can('inventory.view')

  const archive = (p: Product) => void api(`products/${p.id}/${p.archived_at ? 'restore' : 'archive'}/`, { method: 'POST' }).then(reload)
  const actionsFor = (p: Product) => (
    <>
      <Button kind="ghost" onClick={() => { setViewing(null); setLabels([p]) }}><Icon name="tag" size={16} /> Labels</Button>
      {manage && <>
      <Button kind="ghost" onClick={() => { setViewing(null); setEditing(p) }}>Edit</Button>
      <Button kind="ghost" onClick={() => { setViewing(null); setPricing(p) }}>Branch price</Button>
      <Button kind="ghost" onClick={() => { setViewing(null); archive(p) }}>{p.archived_at ? 'Restore' : 'Archive'}</Button>
      </>}
    </>
  )

  return (
    <div>
      <PageHead title="Products" sub="Org-wide catalog: prices, tax and barcodes">
        <span className="input-icon search">
          <Icon name="search" size={16} />
          <input placeholder="Search name, SKU or barcode" value={q} onChange={(e) => setQ(e.target.value)} />
        </span>
        {selected.size > 0 && (
          <>
            <Button onClick={() => setLabels([...selected.values()])}><Icon name="tag" size={16} /> Print labels ({selected.size})</Button>
            <Button kind="ghost" onClick={() => setSelected(new Map())}>Clear selection</Button>
          </>
        )}
        {manage && <Button kind="primary" onClick={() => setEditing('new')}><Icon name="plus" size={16} /> Add product</Button>}
      </PageHead>
      <Guide id="products" title="Your catalog">
        <ul>
          <li><strong>Add product</strong>: name, sale price and VAT are enough to start. For a new product you can enter its <strong>opening stock</strong> for this branch in the same form.</li>
          <li>One catalog for the whole business. A branch can charge a different price: open the product → <strong>Branch price</strong>.</li>
          <li>Stock counts live in <strong>Inventory</strong>, not here. Many products at once: <strong>Import</strong>.</li>
        </ul>
      </Guide>
      <Tabs value={view} onChange={setView} tabs={[['active', 'Active'], ['archived', 'Archived']]} />
      <ErrorBox error={error} />
      <div className="card flush">
        <table className="table">
          <thead>
            <tr>
              <th className="col-check">
                <input type="checkbox" aria-label="Select all shown" checked={allShown} onChange={() => setSelected((m) => {
                  const n = new Map(m)
                  items.forEach((p) => (allShown ? n.delete(p.id) : n.set(p.id, p)))
                  return n
                })} />
              </th>
              <th>Product</th><th>Category</th>
              {showCost && <th className="num">Cost (default)</th>}
              <th className="num">Price</th><th className="num">Branch price</th><th>Tax</th><th className="num">On hand</th><th>Availability</th><th />
            </tr>
          </thead>
          <tbody>
            {items.map((p) => (
              <tr key={p.id} className="clickable" onClick={() => setViewing(p)}>
                <td className="col-check" onClick={(e) => e.stopPropagation()}>
                  <input type="checkbox" aria-label={`Select ${p.name}`} checked={selected.has(p.id)} onChange={() => toggle(p)} />
                </td>
                <td>
                  <div className="cell-user">
                    <ProductThumb src={p.thumb_url} />
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
            {!items.length && !loading && <tr><td colSpan={10} className="empty">No products</td></tr>}
          </tbody>
        </table>
        <LoadMore more={more} loading={loading} />
      </div>
      {viewing && <ProductDetail productId={viewing.id} onClose={() => setViewing(null)} actions={actionsFor(viewing)} />}
      {editing && <ProductForm key={formKey} product={editing === 'new' ? null : editing} onClose={() => setEditing(null)}
        onSaved={(again) => { reload(); if (again) setFormKey((k) => k + 1); else setEditing(null) }} />}
      {labels && <LabelPrint products={labels} onClose={() => setLabels(null)} />}
      {pricing && <BranchPriceForm product={pricing} onClose={() => setPricing(null)} onSaved={() => { setPricing(null); reload() }} />}
    </div>
  )
}

const UNITS = ['pcs', 'pair', 'box', 'packet', 'kg', 'g', 'litre', 'ml', 'metre', 'dozen', 'set']

/** A suggested SKU from the name: "Prada Slides" -> "PRASLI-4821". Editable;
 *  the server rejects a duplicate. */
function suggestSku(name: string) {
  const letters = name.toUpperCase().split(/[^A-Z0-9]+/).filter(Boolean).map((w) => w.slice(0, 3)).join('').slice(0, 6) || 'ITEM'
  return `${letters}-${String(Math.floor(1000 + Math.random() * 9000))}`
}

/** A select with "+ New" that creates the record in place and selects it. */
function QuickSelect({ label, value, onChange, options, create, canCreate, placeholder }: {
  label: string; value: string | number; onChange: (v: string) => void; options: Named[] | null
  create: (name: string) => Promise<Named>; canCreate: boolean; placeholder: string
}) {
  const [adding, setAdding] = useState(false)
  const [name, setName] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const add = async () => {
    if (!name.trim()) return
    setBusy(true)
    setError(null)
    try {
      const made = await create(name.trim())
      onChange(String(made.id))
      setAdding(false)
      setName('')
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="field">
      <span className="field-label">{label}</span>
      {adding ? (
        <div className="quick-add">
          <input autoFocus placeholder={`New ${label.toLowerCase()} name`} value={name} onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); void add() } if (e.key === 'Escape') setAdding(false) }} />
          <Button kind="primary" busy={busy} onClick={() => void add()}>Add</Button>
          <Button kind="ghost" onClick={() => { setAdding(false); setError(null) }}>Cancel</Button>
        </div>
      ) : (
        <div className="quick-add">
          <select value={value} onChange={(e) => onChange(e.target.value)}>
            <option value="">{placeholder}</option>
            {options?.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
          {canCreate && <Button kind="ghost" onClick={() => setAdding(true)}><Icon name="plus" size={16} /> New</Button>}
        </div>
      )}
      {error && <span className="field-hint bad">{error}</span>}
    </div>
  )
}

function ProductForm({ product, onClose, onSaved }: { product: Product | null; onClose: () => void; onSaved: (addAnother: boolean) => void }) {
  const { branch, can, canOrg } = useAuth()
  const cats = useLoad(() => api<Named[]>('categories/'), [])
  const taxCats = useLoad(() => api<{ id: number; code: string; name: string; current_vat_rate: string | null }[]>('compliance/tax-categories/'), [])
  const sups = useLoad(() => api<Named[]>('suppliers/'), [])
  const [f, setF] = useState({
    sku: product?.sku ?? '', name: product?.name ?? '', category: product?.category ?? '', supplier: product?.supplier ?? '',
    unit: product?.unit ?? 'pcs', cost: product ? toRupeesInput(product.cost_price_paisa ?? 0) : '', price: product ? toRupeesInput(product.sale_price_paisa) : '',
    tax_inclusive: product?.tax_inclusive ?? true, tax_category: product?.tax_category ?? '',
    hs_code: product?.hs_code ?? '', excise_rate: product?.excise_rate ?? '0', input_credit_blocked_pct: String(product?.input_credit_blocked_pct ?? 0),
    opening: '',
  })
  const [barcodes, setBarcodes] = useState<Barcode[]>(product?.barcodes ?? [])
  const [camera, setCamera] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<false | 'save' | 'again'>(false)
  // Photo changes are sent after the product itself is saved.
  const [photo, setPhoto] = useState<Blob | null>(null)
  const [removePhoto, setRemovePhoto] = useState(false)
  const [savedId, setSavedId] = useState(product?.id ?? null) // a new product, once created, is updated on retry
  const [stockDone, setStockDone] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)
  const preview = useMemo(() => (photo ? URL.createObjectURL(photo) : null), [photo])
  useEffect(() => () => void (preview && URL.revokeObjectURL(preview)), [preview])
  const shownPhoto = preview ?? (removePhoto ? null : product?.image_url ?? null)
  const set = (k: keyof typeof f, v: string | boolean) => setF((cur) => ({ ...cur, [k]: v }))
  const isNew = !product
  const canStock = isNew && !!branch && can('inventory.adjust')
  const hasMoreTax = !!(product?.hs_code || Number(product?.excise_rate) || product?.input_credit_blocked_pct)

  // New products start on standard VAT (13%), the usual case.
  useEffect(() => {
    if (isNew && !f.tax_category && taxCats.data) {
      const std = taxCats.data.find((c) => c.code === 'VAT13') ?? taxCats.data.find((c) => Number(c.current_vat_rate) === 13)
      if (std) set('tax_category', String(std.id))
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [taxCats.data])

  // Live figures under the prices: VAT inside the price and the margin over cost (both before tax).
  const figures = useMemo(() => {
    const price = toPaisa(f.price)
    const cost = toPaisa(f.cost)
    const rate = Number(taxCats.data?.find((c) => String(c.id) === String(f.tax_category))?.current_vat_rate ?? 0) / 100
    if (price == null || !f.price) return null
    const net = f.tax_inclusive ? price / (1 + rate) : price
    const vat = f.tax_inclusive ? price - net : price * rate
    const margin = cost != null && f.cost ? net - cost : null
    return { vat: Math.round(vat), gross: Math.round(f.tax_inclusive ? price : price + vat), margin: margin == null ? null : Math.round(margin),
      pct: margin == null || net <= 0 ? null : (margin / net) * 100 }
  }, [f.price, f.cost, f.tax_category, f.tax_inclusive, taxCats.data])

  async function pickPhoto(file: File | undefined) {
    if (!file) return
    try {
      setPhoto(await downscaleImage(file))
      setRemovePhoto(false)
    } catch {
      setError('Could not read that image. Use a JPEG, PNG or WebP file.')
    }
  }

  async function save(addAnother: boolean) {
    if (!f.name.trim()) return setError('Give the product a name')
    if (!f.sku.trim()) return setError('Enter a SKU, or press "Suggest" next to it')
    const price = toPaisa(f.price)
    const cost = toPaisa(f.cost || '0')
    if (!f.price.trim()) return setError('Enter the sale price')
    if (price == null) return setError(`Sale price: ${moneyProblem(f.price)}`)
    if (cost == null) return setError(`Cost price: ${moneyProblem(f.cost)}`)
    const opening = f.opening.trim() ? parseWhole(f.opening) ?? 0 : 0
    if (canStock && f.opening.trim() && wholeProblem(f.opening)) return setError(`Opening stock: ${wholeProblem(f.opening)}`)
    setBusy(addAnother ? 'again' : 'save')
    setError(null)
    try {
      const body = {
        sku: f.sku.trim(), name: f.name.trim(), category: f.category || null, supplier: f.supplier || null, unit: f.unit.trim() || 'pcs',
        cost_price_paisa: cost, sale_price_paisa: price, tax_inclusive: f.tax_inclusive,
        tax_category: f.tax_category || null, hs_code: f.hs_code, excise_rate: f.excise_rate || '0',
        input_credit_blocked_pct: Number(f.input_credit_blocked_pct) || 0,
        barcodes: barcodes.filter((b) => b.code.trim()),
      }
      const saved = await api<Product>(savedId ? `products/${savedId}/` : 'products/', { method: savedId ? 'PATCH' : 'POST', body })
      setSavedId(saved.id)
      if (photo) {
        const form = new FormData()
        form.append('image', photo, 'photo')
        await api(`products/${saved.id}/image/`, { form })
      } else if (removePhoto && product?.image_url) {
        await api(`products/${saved.id}/remove-image/`, { method: 'POST' })
      }
      // Opening stock: a logged stock movement at this branch, valued at the default cost.
      if (canStock && opening > 0 && !stockDone) {
        await api('inventory/adjust/', { body: { product: saved.id, qty: opening, reason: 'Opening stock' } })
        setStockDone(true)
      }
      onSaved(addAnother)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal title={product ? `Edit ${product.name}` : 'New product'} onClose={onClose} wide>
      <ErrorBox error={error} />
      <div className="product-form-top">
        <div className="photo-edit photo-edit-stack">
          <ProductThumb src={shownPhoto} large />
          <input ref={fileRef} type="file" accept="image/jpeg,image/png,image/webp" hidden
            onChange={(e) => { void pickPhoto(e.target.files?.[0]); e.target.value = '' }} />
          <Button kind="ghost" onClick={() => fileRef.current?.click()}><Icon name="upload" size={16} /> {shownPhoto ? 'Replace' : 'Photo'}</Button>
          {shownPhoto && <Button kind="ghost" onClick={() => { setPhoto(null); setRemovePhoto(true) }}>Remove</Button>}
        </div>
        <div className="form-grid">
          <Field label="Name"><input autoFocus={isNew} placeholder="e.g. Prada slides, black" value={f.name} onChange={(e) => set('name', e.target.value)} /></Field>
          <div className="field">
            <span className="field-label">SKU</span>
            <div className="quick-add">
              <input placeholder="Your item code" value={f.sku} onChange={(e) => set('sku', e.target.value)} />
              {isNew && <Button kind="ghost" title="Make a code from the name" onClick={() => set('sku', suggestSku(f.name))}>Suggest</Button>}
            </div>
          </div>
          <QuickSelect label="Category" placeholder="No category" value={f.category} onChange={(v) => set('category', v)} options={cats.data}
            canCreate create={async (name) => { const c = await api<Named>('categories/', { body: { name } }); cats.reload(); return c }} />
          <QuickSelect label="Supplier" placeholder="No supplier" value={f.supplier} onChange={(v) => set('supplier', v)} options={sups.data}
            canCreate={canOrg('purchasing.manage') || can('purchasing.manage')}
            create={async (name) => { const c = await api<Named>('suppliers/', { body: { name } }); sups.reload(); return c }} />
          <Field label="Unit" hint="How it's counted and sold">
            <input list="product-units" placeholder="pcs" value={f.unit} onChange={(e) => set('unit', e.target.value)} />
          </Field>
          <datalist id="product-units">{UNITS.map((u) => <option key={u} value={u} />)}</datalist>
        </div>
      </div>

      <h3 className="form-section">Price</h3>
      <div className="form-grid">
        <Field label="Sale price (Rs)" error={moneyProblem(f.price)}><MoneyInput placeholder="0.00" value={f.price} onChange={(v) => set('price', v)} /></Field>
        <Field label="Cost price (Rs)" hint="What you pay the supplier, before VAT" error={moneyProblem(f.cost)}><MoneyInput placeholder="0.00" value={f.cost} onChange={(v) => set('cost', v)} /></Field>
        <Field label="VAT">
          <select value={f.tax_category} onChange={(e) => set('tax_category', e.target.value)}>
            <option value="">Choose…</option>
            {taxCats.data?.map((c) => <option key={c.id} value={c.id}>{c.name}{c.current_vat_rate != null && !c.name.includes('%') ? ` (${Number(c.current_vat_rate)}%)` : ''}</option>)}
          </select>
        </Field>
      </div>
      <label className="check"><input type="checkbox" checked={f.tax_inclusive} onChange={(e) => set('tax_inclusive', e.target.checked)} /> Sale price includes VAT</label>
      {figures && (
        <dl className="price-figures">
          <div><dt>Customer pays</dt><dd className="fig">{npr(figures.gross)}</dd></div>
          <div><dt>VAT in that</dt><dd className="fig">{npr(figures.vat)}</dd></div>
          {figures.margin != null && (
            <div><dt>Your margin</dt><dd className={`fig ${figures.margin < 0 ? 'bad' : ''}`}>{npr(figures.margin)}{figures.pct != null && ` · ${figures.pct.toFixed(1)}%`}</dd></div>
          )}
        </dl>
      )}

      {canStock && (
        <>
          <h3 className="form-section">Opening stock</h3>
          <div className="form-grid">
            <Field label={`Quantity at ${branch!.name}`} hint="Logged as a stock movement. Later stock comes in through Purchasing">
              <input inputMode="numeric" placeholder="0" value={f.opening} onChange={(e) => set('opening', normalizeNumber(e.target.value).replace(/\D/g, ''))} />
            </Field>
          </div>
        </>
      )}

      <h3 className="form-section">Barcodes</h3>
      {barcodes.map((b, i) => (
        <div key={i} className="inline-form">
          <input placeholder="Code" value={b.code} onChange={(e) => setBarcodes(barcodes.map((x, j) => (j === i ? { ...x, code: e.target.value } : x)))} />
          <select value={b.type} onChange={(e) => setBarcodes(barcodes.map((x, j) => (j === i ? { ...x, type: e.target.value } : x)))}>
            {['ean13', 'ean8', 'upca', 'code128', 'other'].map((t) => <option key={t}>{t}</option>)}
          </select>
          <QtyInput min={1} max={100000} title="Units per scan (pack size)" aria-label="Pack size" value={b.pack_qty}
            onChange={(n) => setBarcodes(barcodes.map((x, j) => (j === i ? { ...x, pack_qty: n } : x)))} />
          <Button kind="ghost" onClick={() => setBarcodes(barcodes.filter((_, j) => j !== i))}>Remove</Button>
        </div>
      ))}
      <div className="flags">
        <Button kind="ghost" onClick={() => setCamera(true)}><Icon name="qr" size={16} /> Scan</Button>
        <Button kind="ghost" onClick={() => setBarcodes([...barcodes, { code: '', type: 'ean13', pack_qty: 1 }])}><Icon name="plus" size={16} /> Type a barcode</Button>
        <Button kind="ghost" title="For products without a manufacturer barcode: an EAN-13 in the in-store range (starts with 2)"
          onClick={() => setBarcodes([...barcodes, { code: generateInStoreEan13(), type: 'ean13', pack_qty: 1 }])}>
          No barcode? Make one
        </Button>
      </div>
      {camera && (
        <CameraScanner title="Scan the product's barcode" onClose={() => setCamera(false)} onDetected={(code) => {
          setCamera(false)
          if (!barcodes.some((b) => b.code === code)) setBarcodes([...barcodes, { code, type: isValidEan13(code) ? 'ean13' : 'code128', pack_qty: 1 }])
        }} />
      )}

      <details className="form-more" open={hasMoreTax}>
        <summary>More tax details <span className="muted small">HS code, excise, blocked input credit</span></summary>
        <div className="form-grid">
          <Field label="HS code" hint="Printed on full tax invoices"><input value={f.hs_code} onChange={(e) => set('hs_code', e.target.value)} /></Field>
          <Field label="Excise (%)"><input inputMode="decimal" value={f.excise_rate} onChange={(e) => set('excise_rate', e.target.value)} /></Field>
          <Field label="Blocked input credit" hint="Purchase VAT that can't be claimed">
            <select value={f.input_credit_blocked_pct} onChange={(e) => set('input_credit_blocked_pct', e.target.value)}>
              <option value="0">None</option>
              <option value="100">100% (petrol, alcohol/beverages, entertainment)</option>
              <option value="60">60% (vehicles)</option>
            </select>
          </Field>
        </div>
      </details>

      <div className="modal-foot">
        <Button onClick={onClose}>Cancel</Button>
        {isNew && <Button busy={busy === 'again'} disabled={!!busy} onClick={() => void save(true)}>Save &amp; add another</Button>}
        <Button kind="primary" busy={busy === 'save'} disabled={!!busy} onClick={() => void save(false)}>{isNew ? 'Save product' : 'Save'}</Button>
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
