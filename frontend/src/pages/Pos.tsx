/**
 * POS screen (design doc §12; payment.md Steps 2–3). Online: cash, FonePay,
 * card/wallet/bank against the server; the server issues and renders the tax
 * invoice (full or abbreviated) and records every print.
 * Offline: cash only, priced from the cached catalog and queued for sync
 * (§10.3). In per-branch series mode the till prints a provisional slip and the
 * tax invoice is issued on sync; in per-register mode it numbers locally.
 */
import QRCode from 'qrcode'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useAuth } from '../auth'
import { Icon } from '../components/Icon'
import { Badge, Button, ErrorBox, Field, Modal, Status } from '../components/ui'
import { ApiError, NetworkError, api } from '../lib/api'
import { npr, toBasisPoints, toPaisa, toRupeesInput } from '../lib/money'
import {
  exportQueue, kvGet, kvSet, lastCachedLoginAge, loadCatalog, loadPosConfig, offlineLogin, peekLocalSeq,
  queueOfflineSale, saveCatalog, savePosConfig, syncLocalSeq,
} from '../lib/offline'
import { driverFor, printResult, provisionalSlipHtml } from '../lib/printer'
import { computeTotals, type DiscountType, type Totals } from '../lib/pricing'
import { probe, syncQueue, useOnline } from '../lib/sync'
import type { DocumentCtx, PayMethod, PosConfig, PosProduct, PrintResult, RegisterCfg, Sale, Shift } from '../lib/types'

interface CartItem {
  product: PosProduct
  qty: number
  discount_type: DiscountType
  discount_value: number
  discount_reason: string
}

interface SaleDiscount {
  type: DiscountType
  value: number
  reason: string
}

const NO_DISCOUNT: SaleDiscount = { type: 'none', value: 0, reason: '' }

interface Buyer {
  name: string
  address: string
  pan: string
}
const NO_BUYER: Buyer = { name: '', address: '', pan: '' }

interface Done {
  saleId: number | null // null = offline provisional slip
  doc: DocumentCtx
  change: number
}
/** The offline catalog has no reorder points, so the tile badge uses a flat threshold. */
const LOW_STOCK = 10
const GRID_LIMIT = 60
const uuid = () => crypto.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`

export default function Pos() {
  const { branch, can, me } = useAuth()
  const online = useOnline()
  const branchId = branch!.id
  const [config, setConfig] = useState<PosConfig | null>(null)
  const [catalog, setCatalog] = useState<PosProduct[]>([])
  const [catalogAge, setCatalogAge] = useState<string | null>(null)
  const [registerId, setRegisterId] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [cart, setCart] = useState<CartItem[]>([])
  const [saleDiscount, setSaleDiscount] = useState<SaleDiscount>(NO_DISCOUNT)
  const [modal, setModal] = useState<null | 'cash' | 'fonepay' | 'other' | 'buyer' | 'discount' | 'held' | 'return' | 'close-shift' | 'tools'>(null)
  const [buyer, setBuyer] = useState<Buyer>(NO_BUYER)
  const [done, setDone] = useState<Done | null>(null)
  const [sessionAge, setSessionAge] = useState<number | null>(null)

  const register = config?.registers.find((r) => r.id === registerId)
  const shift = register?.open_shift ?? null

  // --- load config + catalog (server first, cache fallback) -----------------
  const loadAll = useCallback(async () => {
    setError(null)
    try {
      const cfg = await api<PosConfig>('pos/config')
      setConfig(cfg)
      await savePosConfig(cfg)
      for (const r of cfg.registers) await syncLocalSeq(branchId, r.id, cfg.fiscal_year, r.next_receipt_number)
      const cat = await api<{ generated_at: string; products: PosProduct[] }>('pos/catalog/')
      setCatalog(cat.products)
      setCatalogAge(cat.generated_at)
      await saveCatalog(branchId, cat.generated_at, cat.products)
    } catch (e) {
      if (!(e instanceof NetworkError)) {
        setError((e as Error).message)
        return
      }
      const [cfg, cat] = await Promise.all([loadPosConfig(branchId), loadCatalog(branchId)])
      if (!cfg || !cat) {
        setError('Offline, and this register has no cached catalog yet. Connect once to download it.')
        return
      }
      setConfig(cfg)
      setCatalog(cat.products)
      setCatalogAge(cat.generated_at)
    }
  }, [branchId])

  useEffect(() => {
    void loadAll()
    void lastCachedLoginAge().then(setSessionAge)
  }, [loadAll])

  useEffect(() => {
    if (online) void loadAll()
  }, [online, loadAll])

  useEffect(() => {
    if (!config) return
    void kvGet<number>(`register:${branchId}`).then((saved) => {
      const ids = config.registers.map((r) => r.id)
      setRegisterId(saved && ids.includes(saved) ? saved : ids[0] ?? null)
    })
  }, [config, branchId])

  const chooseRegister = (id: number) => {
    setRegisterId(id)
    void kvSet(`register:${branchId}`, id)
  }

  // --- cart -------------------------------------------------------------------
  const addProduct = (p: PosProduct, qty = 1) => {
    setCart((c) => {
      const i = c.findIndex((x) => x.product.id === p.id && x.discount_type === 'none')
      if (i >= 0) return c.map((x, j) => (j === i ? { ...x, qty: x.qty + qty } : x))
      return [...c, { product: p, qty, discount_type: 'none', discount_value: 0, discount_reason: '' }]
    })
  }
  const updateLine = (i: number, patch: Partial<CartItem>) => setCart((c) => c.map((x, j) => (j === i ? { ...x, ...patch } : x)))
  const removeLine = (i: number) => setCart((c) => c.filter((_, j) => j !== i))
  const clearSale = () => {
    setCart([])
    setSaleDiscount(NO_DISCOUNT)
    setBuyer(NO_BUYER)
  }

  const priced = useMemo(() => {
    const lines = cart.map((c) => ({
      unit_price_paisa: c.product.sale_price_paisa, qty: c.qty, tax_rate: c.product.tax_rate,
      tax_inclusive: c.product.tax_inclusive, discount_type: c.discount_type, discount_value: c.discount_value,
      tax_kind: c.product.tax_kind, excise_rate: c.product.excise_rate, levy_type: c.product.levy_type,
      levy_rate: c.product.levy_rate,
    }))
    // Same engine as the server; the digital-payment VAT discount (off unless
    // configured) only applies to the methods IRD's rule covers.
    const bpFor = (m: PayMethod) => (config?.digital_vat_discount_methods.includes(m) ? config.digital_vat_discount_bp : 0)
    try {
      const forMethod = (m: PayMethod) =>
        computeTotals(lines, saleDiscount.type, saleDiscount.value, m === 'cash' ? config?.cash_rounding_paisa ?? 1 : 1, bpFor(m))
      return {
        cash: forMethod('cash'),
        digital: forMethod('fonepay'),
        forMethod,
        error: null as string | null,
      }
    } catch (e) {
      return { cash: null, digital: null, forMethod: null, error: (e as Error).message }
    }
  }, [cart, saleDiscount, config])

  // Rule 17/18 (mirrors services.decide_invoice_type on the server).
  const invoice = useMemo(() => {
    const total = priced.cash?.total_paisa ?? 0
    const limit = config?.abbreviated_invoice_limit_paisa ?? 1_000_000
    const pan = buyer.pan.trim()
    if (pan && !/^\d{9}$/.test(pan)) return { type: 'full' as const, problem: 'Buyer PAN must be 9 digits' }
    if (pan) return { type: 'full' as const, problem: buyer.name.trim() ? null : 'A full tax invoice needs the buyer name' }
    if (total > limit) {
      return {
        type: 'full' as const,
        problem: buyer.name.trim() && buyer.address.trim() ? null
          : `Bills over ${npr(limit)} need a full tax invoice: add the buyer's name and address`,
      }
    }
    return { type: buyer.name.trim() && buyer.address.trim() ? ('full' as const) : ('abbreviated' as const), problem: null }
  }, [priced, buyer, config])

  const discountShareBp = useMemo(() => {
    const t = priced.digital
    if (!t) return 0
    const gross = t.lines.reduce((a, l) => a + l.gross_paisa, 0)
    const given = t.lines.reduce((a, l) => a + l.line_discount_paisa + l.sale_discount_share_paisa, 0)
    return gross ? (given * 10000) / gross : 0
  }, [priced])
  const needsApproval = discountShareBp > (config?.cashier_max_discount_bp ?? 500) && !can('discount.approve')

  const cartPayload = (approver?: { username: string; password: string }) => ({
    lines: cart.map((c) => ({
      product_id: c.product.id, qty: c.qty, discount_type: c.discount_type, discount_value: c.discount_value,
      discount_reason: c.discount_reason,
    })),
    sale_discount_type: saleDiscount.type,
    sale_discount_value: saleDiscount.value,
    sale_discount_reason: saleDiscount.reason,
    buyer_name: buyer.name.trim(),
    buyer_address: buyer.address.trim(),
    buyer_pan: buyer.pan.trim(),
    approver_username: approver?.username ?? '',
    approver_password: approver?.password ?? '',
  })

  // --- finishing a sale ------------------------------------------------------
  /** Ask the server to print (it records original vs copy) and send it to the printer. */
  const printSale = async (saleId: number, kind: 'original' | 'delivery' = 'original') => {
    const result = await api<PrintResult>(`sales/${saleId}/print/`, { body: { kind } })
    await printResult(register, result)
    return result
  }

  const finishOnline = async (sale: Sale, change: number) => {
    if (register && sale.receipt_seq && config?.invoice_series === 'register')
      await syncLocalSeq(branchId, register.id, sale.fiscal_year, sale.receipt_seq + 1)
    clearSale()
    try {
      const result = await printSale(sale.id)
      setDone({ saleId: sale.id, doc: result.document, change })
    } catch (e) {
      const doc = await api<DocumentCtx>(`sales/${sale.id}/receipt/`).catch(() => null)
      if (doc) setDone({ saleId: sale.id, doc, change })
      setError(`Sale ${sale.receipt_number} saved, but printing failed: ${(e as Error).message}`)
    }
  }

  /** Offline cash sale → IndexedDB queue → print. Per-register series: the
   * till's own invoice number. Per-branch series: a provisional slip (not a
   * tax invoice); the server issues the invoice when the till syncs. */
  const offlineCash = async (tendered: number, totals: Totals, key: string, offlineApproved: boolean) => {
    if (!config || !register) throw new Error('No register config cached')
    const created_at = new Date().toISOString()
    const registerSeries = config.invoice_series === 'register'
    const payload = {
      ...cartPayload(),
      lines: cart.map((c) => ({
        product_id: c.product.id, qty: c.qty, discount_type: c.discount_type, discount_value: c.discount_value,
        discount_reason: c.discount_reason, unit_price_paisa: c.product.sale_price_paisa, tax_rate: c.product.tax_rate,
        tax_inclusive: c.product.tax_inclusive,
      })),
      tendered_paisa: tendered, client_total_paisa: totals.total_paisa, offline_discount_approved: offlineApproved,
    }
    const q = await queueOfflineSale({
      idempotency_key: key, branch_id: branchId, register_id: register.id, created_at, fiscal_year: config.fiscal_year,
      payload, provisional: !registerSeries,
    })
    // Best-effort local stock estimate.
    const sold = new Map(cart.map((c) => [c.product.id, c.qty]))
    const next = catalog.map((p) => (sold.has(p.id) ? { ...p, stock_estimate: p.stock_estimate - sold.get(p.id)! } : p))
    setCatalog(next)
    await saveCatalog(branchId, catalogAge ?? created_at, next)
    const seq = String(q.receipt_seq).padStart(6, '0')
    const number = registerSeries
      ? `${config.branch.code}-${register.code}-${config.fiscal_year}-${seq}`
      : `P-${register.id}-${seq}`
    const money = (p: number) => (p / 100).toFixed(2)
    const now = new Date()
    const doc: DocumentCtx = {
      doc: 'invoice', kind: registerSeries ? (invoice.type) : 'provisional',
      title_np: '', title_en: registerSeries ? (invoice.type === 'full' ? 'TAX INVOICE' : 'ABBREVIATED TAX INVOICE')
        : 'PROVISIONAL RECEIPT - NOT A TAX INVOICE',
      number, fiscal_year: config.fiscal_year.replace('-', '/'),
      seller: { ...config.seller, address: config.branch.address, phone: '', branch: config.branch.name },
      buyer: { name: buyer.name, address: buyer.address, pan: buyer.pan },
      issued: { ad: now.toISOString().slice(0, 10), bs: '', time: now.toTimeString().slice(0, 5) },
      register: register.code, cashier: me?.name ?? '', payment_method: 'Cash',
      tendered: money(tendered), change: tendered > totals.total_paisa ? money(tendered - totals.total_paisa) : '',
      lines: cart.map((c, i) => ({ sno: i + 1, description: c.product.name, qty: c.qty,
        rate: money(c.product.sale_price_paisa), amount: money(totals.lines[i].line_total_paisa) })),
      subtotal: money(totals.subtotal_paisa), discount: money(totals.discount_total_paisa),
      taxable_value: money(totals.subtotal_paisa - totals.discount_total_paisa), vat: money(totals.tax_total_paisa),
      round_adjust: totals.round_adjust_paisa ? money(totals.round_adjust_paisa) : '',
      total: money(totals.total_paisa), in_words: registerSeries ? '' : 'Your tax invoice will be issued when this till reconnects.',
      stamp: { label: 'OFFLINE', copy_no: 0 }, open_drawer: true, provisional_ref: registerSeries ? '' : number,
    }
    clearSale()
    setDone({ saleId: null, doc, change: tendered - totals.total_paisa })
    try {
      await driverFor(register).print({ html: provisionalSlipHtml(doc), document: doc })
      await driverFor(register).openDrawer()
    } catch (e) {
      setError(`Sale saved offline, but printing failed: ${(e as Error).message}`)
    }
  }

  // --- scanning / search ------------------------------------------------------
  const [query, setQuery] = useState('')
  const searchRef = useRef<HTMLInputElement>(null)

  // Product tiles: everything when idle, a wider match list while typing.
  const gridAll = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return catalog
    return catalog.filter((p) => p.name.toLowerCase().includes(q) || p.sku.toLowerCase().includes(q) || p.barcodes.some((b) => b.code === q))
  }, [query, catalog])
  const grid = gridAll.slice(0, GRID_LIMIT)
  const gridTotal = gridAll.length
  const lowCount = useMemo(() => catalog.filter((p) => p.stock_estimate <= LOW_STOCK).length, [catalog])
  const cartIds = new Set(cart.map((c) => c.product.id))

  const onScan = (e: React.FormEvent) => {
    e.preventDefault()
    const q = query.trim()
    if (!q) return
    for (const p of catalog) {
      const bc = p.barcodes.find((b) => b.code === q)
      if (bc) {
        addProduct(p, bc.pack_qty)
        setQuery('')
        return
      }
    }
    const bySku = catalog.find((p) => p.sku.toLowerCase() === q.toLowerCase())
    if (bySku) {
      addProduct(bySku)
      setQuery('')
      return
    }
    if (gridAll.length === 1) {
      addProduct(gridAll[0])
      setQuery('')
    }
  }

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'F2') {
        e.preventDefault()
        searchRef.current?.focus()
      }
      if (e.key === 'F9' && cart.length && shift && !invoice.problem) setModal('cash')
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [cart.length, shift, invoice.problem])

  if (!config) {
    return (
      <div>
        <ErrorBox error={error} />
        {!error && <div className="splash">Loading register…</div>}
      </div>
    )
  }
  if (!config.registers.length) return <div className="empty">No active registers at this branch. Ask an admin to add one.</div>
  if (!register) return <div className="splash">Loading register…</div>

  const totals = priced.cash
  const sessionHours = sessionAge != null ? sessionAge / 3600_000 : null

  return (
    <div className="pos">
      <div className="pos-bar">
        <span className="input-icon">
          <Icon name="printer" size={16} />
          <select aria-label="Register" value={registerId ?? ''} onChange={(e) => chooseRegister(Number(e.target.value))}>
            {config.registers.map((r) => (
              <option key={r.id} value={r.id}>{r.name} ({r.code})</option>
            ))}
          </select>
        </span>
        {shift ? (
          <span className="shift-info"><Icon name="clock" size={15} /> Shift by {shift.cashier_name} · opened {new Date(shift.opened_at).toLocaleTimeString()}</span>
        ) : (
          <Badge tone="warn">No open shift</Badge>
        )}
        <span className="spacer" />
        {!online && <Badge tone="bad">Offline — cash only, sales queue for sync</Badge>}
        {!online && sessionHours != null && sessionHours > (config.offline_login_max_age_hours - 4) && (
          <Badge tone="warn">Cached login {sessionHours.toFixed(0)}h old</Badge>
        )}
        <Button kind="ghost" onClick={() => setModal('tools')}><Icon name="settings" size={16} /> Register tools</Button>
        {shift && <Button onClick={() => setModal('close-shift')}><Icon name="lock" size={16} /> Close shift</Button>}
      </div>
      <ErrorBox error={error} />

      {!shift ? (
        <OpenShift register={register!} online={online} onOpened={loadAll} />
      ) : (
        <div className="pos-grid">
          <section className="pos-left">
            <div className="pos-panel">
              <form onSubmit={onScan} className="scan">
                <Icon name="search" />
                <input
                  ref={searchRef}
                  autoFocus
                  placeholder="Scan barcode or search name / SKU (F2)"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                />
              </form>
            </div>
            <div className="pos-stats">
              <div className="tile">
                <div className="tile-icon"><Icon name="cart" size={20} /></div>
                <div className="tile-value">{cart.reduce((a, c) => a + c.qty, 0)}</div>
                <div className="tile-label">Units in bill</div>
              </div>
              <div className="tile">
                <div className="tile-icon tone-info"><Icon name="coins" size={20} /></div>
                <div className="tile-value">{npr(totals?.total_paisa ?? 0)}</div>
                <div className="tile-label">Bill total</div>
              </div>
              <div className="tile">
                <div className="tile-icon tone-warn"><Icon name="alert" size={20} /></div>
                <div className="tile-value">{lowCount}</div>
                <div className="tile-label">Low / out of stock</div>
              </div>
            </div>
            <div className="pos-panel">
              <div className="prod-head">
                <h2><Icon name="package" /> {query.trim() ? 'Search results' : 'Available products'}</h2>
                <Badge>{gridTotal} items</Badge>
              </div>
              {!grid.length && <div className="empty">No products match “{query}”.</div>}
              <div className="prod-grid">
                {grid.map((p) => {
                  const inCart = cartIds.has(p.id)
                  const tag = p.stock_estimate <= 0 ? 'out' : p.stock_estimate <= LOW_STOCK ? 'low' : ''
                  return (
                    <button key={p.id} className={`prod-card ${inCart ? 'in-cart' : ''}`} onClick={() => { addProduct(p); setQuery(''); searchRef.current?.focus() }}>
                      <span className="prod-card-top">
                        <span className="prod-name">{p.name}</span>
                        <span className={`stock-tag ${tag}`}>{p.stock_estimate}</span>
                      </span>
                      <span className="prod-sku">{p.sku}</span>
                      <span className="prod-price">{npr(p.sale_price_paisa)}</span>
                      <span className="prod-add"><Icon name="plus" size={14} /></span>
                    </button>
                  )
                })}
              </div>
              {gridTotal > grid.length && <div className="muted small center-text" style={{ marginTop: 10 }}>Showing {grid.length} of {gridTotal} — search to narrow down.</div>}
            </div>
          </section>

          <aside className="pos-right">
            <div className="bill-head">
              <h2><Icon name="cart" /> Current bill</h2>
              <Badge tone={cart.length ? 'good' : 'neutral'}>{cart.length} item{cart.length === 1 ? '' : 's'}</Badge>
            </div>
            <div className="bill-buyer invoice-box">
              <div className="invoice-type">
                {invoice.type === 'full' ? <Badge tone="info">Tax invoice · कर बीजक</Badge> : <Badge>Abbreviated tax invoice</Badge>}
                <button className="link" onClick={() => setModal('buyer')}>
                  {buyer.name ? `${buyer.name}${buyer.pan ? ` · PAN ${buyer.pan}` : ''}` : '+ Add buyer / PAN'}
                </button>
              </div>
              {invoice.problem && cart.length > 0 && <div className="alert alert-warn small" style={{ margin: 0 }}>{invoice.problem}</div>}
            </div>
            <div className="cart">
              {cart.map((c, i) => (
                <CartRow key={i} item={c} line={totals?.lines[i]} onChange={(p) => updateLine(i, p)} onRemove={() => removeLine(i)} />
              ))}
              {!cart.length && (
                <div className="cart-empty">
                  <Icon name="cart" size={36} />
                  Scan or tap a product to start a sale.
                </div>
              )}
            </div>
            <div className="bill-foot">
              <div className="totals">
                <Row label="Subtotal (excl. tax)" value={totals?.subtotal_paisa ?? 0} />
                <Row label="Discount" value={-(totals?.discount_total_paisa ?? 0)} />
                {totals && Object.entries(totals.tax_by_rate).map(([rate, amt]) => <Row key={rate} label={`VAT ${rate}%`} value={amt} />)}
                {!!totals?.excise_total_paisa && <Row label="Excise" value={totals.excise_total_paisa} />}
                {!!totals?.hst_total_paisa && <Row label="Health Service Tax" value={totals.hst_total_paisa} />}
                {!!totals?.esf_total_paisa && <Row label="Education Service Fee" value={totals.esf_total_paisa} />}
                {!!totals?.round_adjust_paisa && <Row label="Rounding" value={totals.round_adjust_paisa} />}
                <div className="grand">
                  <span>Total</span>
                  <span>{npr(totals?.total_paisa ?? 0)}</span>
                </div>
                {priced.error && <div className="alert alert-error">{priced.error}</div>}
                {saleDiscount.type !== 'none' && (
                  <div className="muted small">
                    Sale discount: {saleDiscount.type === 'percent' ? `${saleDiscount.value / 100}%` : npr(saleDiscount.value)} — {saleDiscount.reason}
                  </div>
                )}
                {needsApproval && <Badge tone="warn">Discount needs manager approval</Badge>}
              </div>
              <div className="pay-buttons">
                <Button kind="primary" className="btn-xl" disabled={!cart.length || !!priced.error || !!invoice.problem} onClick={() => setModal('cash')}>
                  <Icon name="wallet" /> Cash (F9)
                </Button>
                <Button
                  className="btn-xl"
                  disabled={!cart.length || !!priced.error || !online || !!invoice.problem}
                  title={online ? '' : 'FonePay needs internet'}
                  onClick={() => setModal('fonepay')}
                >
                  <Icon name="qr" /> FonePay
                </Button>
              </div>
              <Button
                disabled={!cart.length || !!priced.error || !online || !!invoice.problem}
                title={online ? '' : 'Needs internet'}
                onClick={() => setModal('other')}
              >
                <Icon name="card" size={16} /> Card / Wallet / Bank
              </Button>
              <div className="pos-actions">
                <Button disabled={!cart.length} onClick={() => setModal('discount')}><Icon name="percent" size={16} /> Discount</Button>
                <Button disabled={!online} title={online ? '' : 'Needs internet'} onClick={() => setModal('held')}>
                  <Icon name="pause" size={16} /> {cart.length ? 'Hold' : 'Recall'}
                </Button>
                <Button disabled={!online} title={online ? '' : 'Returns need the server to verify the sale'} onClick={() => setModal('return')}>
                  <Icon name="undo" size={16} /> Return
                </Button>
                <Button kind="danger" disabled={!cart.length} onClick={clearSale}><Icon name="x" size={16} /> Clear</Button>
              </div>
            </div>
          </aside>
        </div>
      )}

      {modal === 'cash' && totals && register && (
        <CashModal
          total={totals.total_paisa}
          needsApproval={needsApproval}
          online={online}
          onClose={() => setModal(null)}
          onPay={async (tendered, approver) => {
            const key = uuid()
            if (online) {
              try {
                const sale = await api<Sale>('pos/checkout', {
                  body: { ...cartPayload(approver), register_id: register.id, idempotency_key: key, tendered_paisa: tendered },
                })
                setModal(null)
                await finishOnline(sale, sale.payments[0]?.change_paisa ?? 0)
                return
              } catch (e) {
                if (!(e instanceof NetworkError)) throw e
                void probe()
              }
            }
            await offlineCash(tendered, totals, key, !!approver)
            setModal(null)
          }}
        />
      )}
      {modal === 'fonepay' && priced.digital && register && (
        <FonepayModal
          total={priced.digital.total_paisa}
          needsApproval={needsApproval}
          mock={config.fonepay_backend === 'mock'}
          start={(approver, key) =>
            api<Sale & { qr_payload: string; prn: string }>('pos/checkout/initiate', {
              body: { ...cartPayload(approver), register_id: register.id, idempotency_key: key },
            })
          }
          onClose={() => setModal(null)}
          onPaid={async (sale) => {
            setModal(null)
            if (sale.stock_conflict) setError(`Sale ${sale.receipt_number} paid but stock was short — flagged for manager review.`)
            await finishOnline(sale, 0)
          }}
        />
      )}
      {modal === 'other' && priced.forMethod && register && (
        <OtherPaymentModal
          totalFor={(m) => priced.forMethod!(m).total_paisa}
          needsApproval={needsApproval}
          onClose={() => setModal(null)}
          onPay={async (method, ref, approver) => {
            const sale = await api<Sale>('pos/checkout', {
              body: { ...cartPayload(approver), register_id: register.id, idempotency_key: uuid(), method, payment_ref: ref },
            })
            setModal(null)
            await finishOnline(sale, 0)
          }}
        />
      )}
      {modal === 'buyer' && (
        <BuyerModal value={buyer} limit={config.abbreviated_invoice_limit_paisa} onClose={() => setModal(null)}
          onSave={(b) => { setBuyer(b); setModal(null) }} />
      )}
      {modal === 'discount' && (
        <SaleDiscountModal value={saleDiscount} onClose={() => setModal(null)} onSave={(d) => { setSaleDiscount(d); setModal(null) }} />
      )}
      {modal === 'held' && register && (
        <HeldModal
          register={register}
          cart={cart}
          saleDiscount={saleDiscount}
          catalog={catalog}
          onClose={() => setModal(null)}
          onHeld={() => { clearSale(); setModal(null) }}
          onRecall={(items, d) => { setCart(items); setSaleDiscount(d); setModal(null) }}
        />
      )}
      {modal === 'return' && register && (
        <ReturnModal register={register} threshold={config.return_approval_threshold_paisa} onClose={() => setModal(null)}
          onDone={() => void loadAll()} />
      )}
      {modal === 'close-shift' && shift && (
        <CloseShiftModal shift={shift} online={online} onClose={() => setModal(null)} onClosed={() => { setModal(null); void loadAll() }} />
      )}
      {modal === 'tools' && register && (
        <ToolsModal register={register} config={config} catalogAge={catalogAge} onClose={() => setModal(null)} onRefresh={loadAll} />
      )}
      {done && (
        <DoneModal
          done={done}
          deliveryLimit={config.delivery_copy_limit_paisa}
          onPrint={async (kind) => {
            if (done.saleId == null) return driverFor(register).print({ html: provisionalSlipHtml(done.doc), document: done.doc })
            const r = await printSale(done.saleId, kind)
            setDone({ ...done, doc: r.document })
          }}
          onClose={() => { setDone(null); searchRef.current?.focus() }}
        />
      )}
    </div>
  )
}

function Row({ label, value }: { label: string; value: number }) {
  return (
    <div className="total-row">
      <span>{label}</span>
      <span>{npr(value)}</span>
    </div>
  )
}

function CartRow({ item, line, onChange, onRemove }: {
  item: CartItem
  line?: Totals['lines'][number]
  onChange: (p: Partial<CartItem>) => void
  onRemove: () => void
}) {
  const [editing, setEditing] = useState(false)
  return (
    <div className="cart-line">
      <div className="cart-line-top">
        <span className="cart-line-name">{item.product.name}</span>
        <span className="cart-line-amt">{npr(line?.line_total_paisa ?? 0)}</span>
      </div>
      <div className="cart-line-bottom">
        <div className="cart-line-meta">
          <span>{npr(item.product.sale_price_paisa)} each · VAT {Number(item.product.tax_rate)}%{item.product.tax_inclusive ? ' incl.' : ''}</span>
          <button className="link small" onClick={() => setEditing((v) => !v)}>
            {line?.line_discount_paisa ? `−${npr(line.line_discount_paisa)}` : '+ discount'}
          </button>
        </div>
        <div className="qty">
          <button onClick={() => onChange({ qty: Math.max(1, item.qty - 1) })} aria-label="Decrease"><Icon name="minus" size={14} /></button>
          <input
            aria-label="Quantity"
            value={item.qty}
            onChange={(e) => {
              const n = parseInt(e.target.value, 10)
              if (n > 0) onChange({ qty: n })
            }}
          />
          <button onClick={() => onChange({ qty: item.qty + 1 })} aria-label="Increase"><Icon name="plus" size={14} /></button>
        </div>
        <button className="icon-btn danger" onClick={onRemove} aria-label="Remove line"><Icon name="trash" size={16} /></button>
      </div>
      {editing && (
        <DiscountEditor
          value={{ type: item.discount_type, value: item.discount_value, reason: item.discount_reason }}
          onSave={(d) => {
            onChange({ discount_type: d.type, discount_value: d.value, discount_reason: d.reason })
            setEditing(false)
          }}
        />
      )}
    </div>
  )
}

function DiscountEditor({ value, onSave }: { value: SaleDiscount; onSave: (d: SaleDiscount) => void }) {
  const [type, setType] = useState<DiscountType>(value.type === 'none' ? 'percent' : value.type)
  const [raw, setRaw] = useState(value.type === 'none' ? '' : value.type === 'percent' ? String(value.value / 100) : toRupeesInput(value.value))
  const [reason, setReason] = useState(value.reason)
  const parsed = type === 'percent' ? toBasisPoints(raw) : toPaisa(raw)
  return (
    <div className="inline-form">
      <select value={type} onChange={(e) => setType(e.target.value as DiscountType)}>
        <option value="percent">%</option>
        <option value="amount">Rs</option>
      </select>
      <input placeholder={type === 'percent' ? 'e.g. 10' : 'e.g. 50.00'} value={raw} onChange={(e) => setRaw(e.target.value)} />
      <input placeholder="Reason (required)" value={reason} onChange={(e) => setReason(e.target.value)} />
      <Button kind="primary" disabled={parsed == null || (!!parsed && !reason.trim())} onClick={() => onSave({ type: parsed ? type : 'none', value: parsed ?? 0, reason: reason.trim() })}>
        Apply
      </Button>
      <Button onClick={() => onSave(NO_DISCOUNT)}>Remove</Button>
    </div>
  )
}

function SaleDiscountModal({ value, onClose, onSave }: { value: SaleDiscount; onClose: () => void; onSave: (d: SaleDiscount) => void }) {
  return (
    <Modal title="Sale discount (applied before tax)" onClose={onClose}>
      <DiscountEditor value={value} onSave={onSave} />
    </Modal>
  )
}

function ApproverFields({ value, onChange }: { value: { username: string; password: string }; onChange: (v: { username: string; password: string }) => void }) {
  return (
    <div className="approver">
      <div className="muted small">Manager approval</div>
      <input placeholder="Manager username" value={value.username} onChange={(e) => onChange({ ...value, username: e.target.value })} />
      <input type="password" placeholder="Manager password" value={value.password} onChange={(e) => onChange({ ...value, password: e.target.value })} />
    </div>
  )
}

function CashModal({ total, needsApproval, online, onClose, onPay }: {
  total: number
  needsApproval: boolean
  online: boolean
  onClose: () => void
  onPay: (tendered: number, approver?: { username: string; password: string }) => Promise<void>
}) {
  const [raw, setRaw] = useState(toRupeesInput(total))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [approver, setApprover] = useState({ username: '', password: '' })
  const tendered = toPaisa(raw)
  const quick = [total, ...[500, 1000, 2000, 5000].map((r) => r * 100).filter((x) => x > total)].slice(0, 4)

  async function pay() {
    if (tendered == null || tendered < total) return
    setBusy(true)
    setError(null)
    try {
      let appr = needsApproval ? approver : undefined
      if (appr && !online) {
        // Offline: verify the manager against their cached login on this device; flagged for review on sync.
        const m = await offlineLogin(appr.username, appr.password, 24)
        if (!m.branches.some((b) => b.permissions.includes('discount.approve'))) throw new Error('That user cannot approve discounts')
        appr = { ...appr, password: '' }
      }
      await onPay(tendered, appr)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : (e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal title="Cash payment" onClose={onClose}>
      <div className="big-total">{npr(total)}</div>
      {!online && <div className="alert alert-warn">Offline — this sale will be numbered locally and synced later.</div>}
      <ErrorBox error={error} />
      <Field label="Tendered (Rs)">
        <input autoFocus inputMode="decimal" value={raw} onChange={(e) => setRaw(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && void pay()} />
      </Field>
      <div className="quick">
        {quick.map((q) => (
          <Button key={q} onClick={() => setRaw(toRupeesInput(q))}>{npr(q)}</Button>
        ))}
      </div>
      {tendered != null && tendered >= total && <div className="change">Change: {npr(tendered - total)}</div>}
      {needsApproval && <ApproverFields value={approver} onChange={setApprover} />}
      <div className="modal-foot">
        <Button onClick={onClose}>Cancel</Button>
        <Button kind="primary" busy={busy} disabled={tendered == null || tendered < total || (needsApproval && !approver.username)} onClick={() => void pay()}>
          Complete sale
        </Button>
      </div>
    </Modal>
  )
}

function FonepayModal({ total, needsApproval, mock, start, onClose, onPaid }: {
  total: number
  needsApproval: boolean
  mock: boolean
  start: (approver: { username: string; password: string } | undefined, key: string) => Promise<Sale & { qr_payload: string; prn: string }>
  onClose: () => void
  onPaid: (sale: Sale) => Promise<void>
}) {
  const [sale, setSale] = useState<(Sale & { qr_payload: string; prn: string }) | null>(null)
  const [qr, setQr] = useState<string | null>(null)
  const [status, setStatus] = useState('Waiting for customer to scan…')
  const [error, setError] = useState<string | null>(null)
  const [approver, setApprover] = useState({ username: '', password: '' })
  const [busy, setBusy] = useState(false)
  // One key per QR attempt: a repeated start (double effect, double click)
  // gets the same open sale back instead of creating a second one.
  const key = useRef(uuid())
  const starting = useRef(false)

  async function begin() {
    if (starting.current) return
    starting.current = true
    setBusy(true)
    setError(null)
    try {
      const s = await start(needsApproval ? approver : undefined, key.current)
      setSale(s)
      setQr(await QRCode.toDataURL(s.qr_payload || s.prn, { width: 260, margin: 1 }))
    } catch (e) {
      starting.current = false
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  useEffect(() => {
    if (!needsApproval) void begin()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Poll the server, which polls FonePay by PRN (§10.2 polling path).
  useEffect(() => {
    if (!sale) return
    let stop = false
    const tick = async () => {
      try {
        const r = await api<{ status: string; payment_status: string; sale: Sale | null }>(`pos/sale/${sale.id}/payment-status`)
        if (stop) return
        if (r.status === 'paid' && r.sale) {
          stop = true
          await onPaid(r.sale)
          return
        }
        if (r.status === 'voided') {
          setStatus(`Payment ${r.payment_status}. No charge was recorded.`)
          stop = true
          return
        }
        if (r.status === 'payment_orphan') {
          setStatus('Paid amount does not match the total — call a manager (sale flagged for review).')
          stop = true
          return
        }
      } catch {
        /* transient; keep polling */
      }
      if (!stop) setTimeout(tick, 3000)
    }
    const t = setTimeout(tick, 3000)
    return () => {
      stop = true
      clearTimeout(t)
    }
  }, [sale, onPaid])

  return (
    <Modal title="FonePay QR" onClose={onClose}>
      <div className="big-total">{npr(total)}</div>
      <ErrorBox error={error} />
      {!sale && needsApproval && (
        <>
          <ApproverFields value={approver} onChange={setApprover} />
          <div className="modal-foot">
            <Button kind="primary" busy={busy} onClick={() => void begin()}>Show QR</Button>
          </div>
        </>
      )}
      {sale && (
        <div className="qr">
          {qr && <img src={qr} alt="FonePay QR code" width={260} height={260} />}
          <div className="muted small">PRN {sale.prn} · expires {sale.expires_at ? new Date(sale.expires_at).toLocaleTimeString() : ''}</div>
          <div>{status}</div>
          {mock && (
            <div className="mock-box">
              <span className="muted small">Mock gateway (dev):</span>
              <Button onClick={() => void api(`pos/fonepay/mock-pay/${sale.prn}`, { body: {} })}>Simulate payment</Button>
              <Button onClick={() => void api(`pos/fonepay/mock-pay/${sale.prn}`, { body: { fail: true } })}>Simulate failure</Button>
            </div>
          )}
        </div>
      )}
      <div className="modal-foot">
        <Button onClick={onClose}>Close (sale expires if unpaid)</Button>
      </div>
    </Modal>
  )
}

function DoneModal({ done, deliveryLimit, onPrint, onClose }: {
  done: Done
  deliveryLimit: number
  onPrint: (kind: 'original' | 'delivery') => Promise<void>
  onClose: () => void
}) {
  const [error, setError] = useState<string | null>(null)
  const d = done.doc
  const print = (kind: 'original' | 'delivery') => onPrint(kind).catch((e) => setError((e as Error).message))
  const deliveryOk = done.saleId != null && (d.total_paisa ?? 0) > deliveryLimit
  return (
    <Modal title={done.saleId == null ? 'Sale saved offline' : 'Sale complete'} onClose={onClose}>
      <ErrorBox error={error} />
      <div className="done">
        <div className="muted">{d.title_en} {d.number}</div>
        {d.kind === 'provisional' && (
          <div className="alert alert-warn small">Provisional slip, not a tax invoice. The invoice is issued when this till reconnects. Print it from Sales → Unprinted.</div>
        )}
        <div className="big-total">Rs {d.total}</div>
        {done.change > 0 && <div className="change">Change due: {npr(done.change)}</div>}
        {d.stamp.label && done.saleId != null && <div className="muted small">Last print: {d.stamp.label}</div>}
      </div>
      <div className="modal-foot">
        <Button onClick={() => void print('original')}>{done.saleId == null ? 'Reprint slip' : 'Reprint (copy)'}</Button>
        {deliveryOk && <Button onClick={() => void print('delivery')}>Delivery copy</Button>}
        <Button kind="primary" autoFocus onClick={onClose}>New sale</Button>
      </div>
    </Modal>
  )
}

function BuyerModal({ value, limit, onClose, onSave }: { value: Buyer; limit: number; onClose: () => void; onSave: (b: Buyer) => void }) {
  const [b, setB] = useState(value)
  const panBad = !!b.pan.trim() && !/^\d{9}$/.test(b.pan.trim())
  return (
    <Modal title="Buyer details" onClose={onClose}>
      <p className="muted small">
        A buyer PAN makes this a full tax invoice (कर बीजक). Bills over {npr(limit)} always need the buyer's name and address.
      </p>
      <Field label="Name"><input autoFocus value={b.name} onChange={(e) => setB({ ...b, name: e.target.value })} /></Field>
      <Field label="Address"><input value={b.address} onChange={(e) => setB({ ...b, address: e.target.value })} /></Field>
      <Field label="PAN / VAT no. (9 digits)" hint={panBad ? 'PAN must be 9 digits' : undefined}>
        <input inputMode="numeric" maxLength={9} value={b.pan} onChange={(e) => setB({ ...b, pan: e.target.value.replace(/\D/g, '') })} />
      </Field>
      <div className="modal-foot">
        <Button onClick={() => onSave(NO_BUYER)}>Clear</Button>
        <Button kind="primary" disabled={panBad} onClick={() => onSave(b)}>Save</Button>
      </div>
    </Modal>
  )
}

function OtherPaymentModal({ totalFor, needsApproval, onClose, onPay }: {
  totalFor: (m: PayMethod) => number
  needsApproval: boolean
  onClose: () => void
  onPay: (method: PayMethod, ref: string, approver?: { username: string; password: string }) => Promise<void>
}) {
  const [method, setMethod] = useState<PayMethod>('card')
  const [ref, setRef] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [approver, setApprover] = useState({ username: '', password: '' })
  const total = totalFor(method)
  return (
    <Modal title="Card / wallet / bank payment" onClose={onClose}>
      <div className="big-total">{npr(total)}</div>
      {total < totalFor('cash') && <div className="muted small center-text">Includes the digital-payment VAT discount</div>}
      <ErrorBox error={error} />
      <Field label="Method">
        <select value={method} onChange={(e) => setMethod(e.target.value as PayMethod)}>
          <option value="card">Card</option>
          <option value="wallet">Wallet (eSewa, Khalti…)</option>
          <option value="bank">Bank transfer</option>
        </select>
      </Field>
      <Field label="Reference (slip / transaction ID)">
        <input autoFocus value={ref} onChange={(e) => setRef(e.target.value)} />
      </Field>
      {needsApproval && <ApproverFields value={approver} onChange={setApprover} />}
      <div className="modal-foot">
        <Button onClick={onClose}>Cancel</Button>
        <Button kind="primary" busy={busy} disabled={!ref.trim() || (needsApproval && !approver.username)} onClick={async () => {
          setBusy(true)
          setError(null)
          try {
            await onPay(method, ref.trim(), needsApproval ? approver : undefined)
          } catch (e) {
            setError((e as Error).message)
          } finally {
            setBusy(false)
          }
        }}>Record payment</Button>
      </div>
    </Modal>
  )
}

function OpenShift({ register, online, onOpened }: { register: RegisterCfg; online: boolean; onOpened: () => Promise<void> }) {
  const [raw, setRaw] = useState('0.00')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const cash = toPaisa(raw)
  return (
    <div className="card narrow">
      <h2>Open shift on {register.name}</h2>
      <p className="muted">Count the drawer and enter the starting cash.</p>
      {!online && <div className="alert alert-warn">Opening a shift needs a connection.</div>}
      <ErrorBox error={error} />
      <Field label="Opening cash (Rs)">
        <input value={raw} onChange={(e) => setRaw(e.target.value)} inputMode="decimal" />
      </Field>
      <Button
        kind="primary"
        busy={busy}
        disabled={cash == null || !online}
        onClick={async () => {
          setBusy(true)
          try {
            await api('pos/shifts/open/', { body: { register_id: register.id, opening_cash_paisa: cash } })
            await onOpened()
          } catch (e) {
            setError((e as Error).message)
          } finally {
            setBusy(false)
          }
        }}
      >
        Open shift
      </Button>
    </div>
  )
}

function CloseShiftModal({ shift, online, onClose, onClosed }: { shift: Shift; online: boolean; onClose: () => void; onClosed: () => void }) {
  const [raw, setRaw] = useState('')
  const [result, setResult] = useState<Shift | null>(null)
  const [expected, setExpected] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)
  const counted = toPaisa(raw)
  useEffect(() => {
    if (online) void api<Shift>(`pos/shifts/${shift.id}/summary/`).then((s) => setExpected(s.expected_cash_paisa)).catch(() => undefined)
  }, [shift.id, online])
  return (
    <Modal title="Close shift" onClose={onClose}>
      {!online && <div className="alert alert-warn">Offline — closing is saved on this device and posted when the connection returns.</div>}
      <ErrorBox error={error} />
      {result ? (
        <div>
          <Row label="Expected cash" value={result.expected_cash_paisa ?? 0} />
          <Row label="Counted cash" value={result.counted_cash_paisa ?? 0} />
          <div className={`grand ${result.variance_paisa ? 'text-bad' : ''}`}>
            <span>Variance</span>
            <span>{npr(result.variance_paisa)}</span>
          </div>
          <div className="modal-foot">
            <Button kind="primary" onClick={onClosed}>Done</Button>
          </div>
        </div>
      ) : (
        <>
          <p className="muted">Count the drawer without looking at the expected figure.</p>
          <Field label="Counted cash (Rs)">
            <input autoFocus value={raw} onChange={(e) => setRaw(e.target.value)} inputMode="decimal" />
          </Field>
          {expected != null && <details className="muted small"><summary>Show expected</summary>{npr(expected)}</details>}
          <div className="modal-foot">
            <Button onClick={onClose}>Cancel</Button>
            <Button
              kind="primary"
              disabled={counted == null}
              onClick={async () => {
                try {
                  if (!online) {
                    await kvSet(`shiftclose:${shift.id}`, { shift_id: shift.id, counted_cash_paisa: counted })
                    onClosed()
                    return
                  }
                  await syncQueue() // post any queued sales first so expected cash is complete
                  setResult(await api<Shift>(`pos/shifts/${shift.id}/close/`, { body: { counted_cash_paisa: counted } }))
                } catch (e) {
                  setError((e as Error).message)
                }
              }}
            >
              Close shift
            </Button>
          </div>
        </>
      )}
    </Modal>
  )
}

function HeldModal({ register, cart, saleDiscount, catalog, onClose, onHeld, onRecall }: {
  register: RegisterCfg
  cart: CartItem[]
  saleDiscount: SaleDiscount
  catalog: PosProduct[]
  onClose: () => void
  onHeld: () => void
  onRecall: (items: CartItem[], d: SaleDiscount) => void
}) {
  const [held, setHeld] = useState<{ id: number; label: string; cart: { items: { product_id: number; qty: number; discount_type: DiscountType; discount_value: number; discount_reason: string }[]; sale_discount: SaleDiscount }; created_at: string }[]>([])
  const [label, setLabel] = useState('')
  const [error, setError] = useState<string | null>(null)
  const load = useCallback(() => void api<typeof held>(`pos/held/?register=${register.id}`).then(setHeld).catch((e) => setError(e.message)), [register.id])
  useEffect(load, [load])
  const byId = new Map(catalog.map((p) => [p.id, p]))
  return (
    <Modal title="Hold / recall" onClose={onClose}>
      <ErrorBox error={error} />
      {cart.length > 0 && (
        <div className="inline-form">
          <input placeholder="Label (e.g. customer name)" value={label} onChange={(e) => setLabel(e.target.value)} />
          <Button
            kind="primary"
            onClick={async () => {
              await api('pos/held/', {
                body: {
                  register: register.id, label,
                  cart: { items: cart.map((c) => ({ product_id: c.product.id, qty: c.qty, discount_type: c.discount_type, discount_value: c.discount_value, discount_reason: c.discount_reason })), sale_discount: saleDiscount },
                },
              })
              onHeld()
            }}
          >
            Hold current sale
          </Button>
        </div>
      )}
      <h3>Held sales</h3>
      {!held.length && <div className="empty">Nothing on hold.</div>}
      {held.map((h) => (
        <div key={h.id} className="list-row">
          <span>
            <strong>{h.label || `Held #${h.id}`}</strong> <span className="muted small">{h.cart.items.length} item(s) · {new Date(h.created_at).toLocaleTimeString()}</span>
          </span>
          <span>
            <Button
              disabled={cart.length > 0}
              title={cart.length ? 'Hold or clear the current sale first' : ''}
              onClick={async () => {
                const items = h.cart.items.flatMap((i) => {
                  const p = byId.get(i.product_id)
                  return p ? [{ product: p, qty: i.qty, discount_type: i.discount_type, discount_value: i.discount_value, discount_reason: i.discount_reason }] : []
                })
                await api(`pos/held/${h.id}/`, { method: 'DELETE' })
                onRecall(items, h.cart.sale_discount ?? NO_DISCOUNT)
              }}
            >
              Recall
            </Button>
          </span>
        </div>
      ))}
    </Modal>
  )
}

function ReturnModal({ register, threshold, onClose, onDone }: { register: RegisterCfg; threshold: number; onClose: () => void; onDone: () => void }) {
  const [number, setNumber] = useState('')
  const [sale, setSale] = useState<Sale | null>(null)
  const [qty, setQty] = useState<Record<number, number>>({})
  const [reason, setReason] = useState('')
  const [approver, setApprover] = useState({ username: '', password: '' })
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<{ number: string; total_paisa: number } | null>(null)
  const { can } = useAuth()

  const estimate = sale ? sale.lines.reduce((a, l) => a + Math.round((l.line_total_paisa * (qty[l.id] ?? 0)) / l.qty), 0) : 0
  const needsApproval = estimate > threshold && !can('returns.approve')

  return (
    <Modal title="Return / refund" onClose={onClose} wide>
      <ErrorBox error={error} />
      {result ? (
        <>
          <div className="alert alert-good">Credit note {result.number} — refund {npr(result.total_paisa)}</div>
          <div className="modal-foot"><Button kind="primary" onClick={onClose}>Done</Button></div>
        </>
      ) : !sale ? (
        <form
          className="inline-form"
          onSubmit={async (e) => {
            e.preventDefault()
            setError(null)
            try {
              setSale(await api<Sale>(`sales/lookup/?receipt=${encodeURIComponent(number.trim())}`))
            } catch (err) {
              setError(err instanceof ApiError && err.status === 404 ? 'No sale with that receipt number at this branch' : (err as Error).message)
            }
          }}
        >
          <input autoFocus placeholder="Receipt number" value={number} onChange={(e) => setNumber(e.target.value)} />
          <Button kind="primary" type="submit">Find sale</Button>
        </form>
      ) : (
        <>
          <div className="muted">
            {sale.receipt_number} · {new Date(sale.paid_at ?? sale.opened_at).toLocaleString()} · <Status value={sale.status} />
          </div>
          <table className="table">
            <thead>
              <tr><th>Item</th><th className="num">Sold</th><th className="num">Returned</th><th className="num">Line total</th><th className="num">Return qty</th></tr>
            </thead>
            <tbody>
              {sale.lines.map((l) => (
                <tr key={l.id}>
                  <td>{l.product_name}</td>
                  <td className="num">{l.qty}</td>
                  <td className="num">{l.returned_qty}</td>
                  <td className="num">{npr(l.line_total_paisa)}</td>
                  <td className="num">
                    <input
                      className="input-sm"
                      type="number"
                      min={0}
                      max={l.qty - l.returned_qty}
                      value={qty[l.id] ?? 0}
                      onChange={(e) => setQty({ ...qty, [l.id]: Math.min(l.qty - l.returned_qty, Math.max(0, Number(e.target.value))) })}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="total-row"><span>Estimated refund</span><span>{npr(estimate)}</span></div>
          <Field label="Reason">
            <input value={reason} onChange={(e) => setReason(e.target.value)} />
          </Field>
          {needsApproval && <ApproverFields value={approver} onChange={setApprover} />}
          <div className="modal-foot">
            <Button onClick={() => setSale(null)}>Back</Button>
            <Button
              kind="primary"
              disabled={!estimate || !reason.trim()}
              onClick={async () => {
                setError(null)
                try {
                  const cn = await api<{ number: string; total_paisa: number }>(`sales/${sale.id}/returns/`, {
                    body: {
                      register_id: register.id, reason, refund_method: 'cash',
                      lines: Object.entries(qty).filter(([, q]) => q > 0).map(([id, q]) => ({ sale_line_id: Number(id), qty: q })),
                      approver_username: approver.username, approver_password: approver.password,
                    },
                  })
                  setResult(cn)
                  onDone()
                } catch (e) {
                  setError((e as Error).message)
                }
              }}
            >
              Issue credit note
            </Button>
          </div>
        </>
      )}
    </Modal>
  )
}

function ToolsModal({ register, config, catalogAge, onClose, onRefresh }: {
  register: RegisterCfg
  config: PosConfig
  catalogAge: string | null
  onClose: () => void
  onRefresh: () => Promise<void>
}) {
  const [localSeq, setLocalSeq] = useState<number | null>(null)
  useEffect(() => void peekLocalSeq(config.branch.id, register.id, config.fiscal_year).then(setLocalSeq), [config, register])
  return (
    <Modal title={`Register ${register.code}`} onClose={onClose}>
      <dl className="dl">
        <dt>Fiscal year</dt><dd>{config.fiscal_year}</dd>
        <dt>Next receipt (local)</dt><dd>{localSeq ?? '…'}</dd>
        <dt>Next receipt (server, last sync)</dt><dd>{register.next_receipt_number}</dd>
        <dt>Catalog downloaded</dt><dd>{catalogAge ? new Date(catalogAge).toLocaleString() : 'never'}</dd>
        <dt>Printer</dt><dd>{register.printer_driver}{register.printer_config?.url ? ` → ${register.printer_config.url}` : ''}</dd>
      </dl>
      <div className="modal-foot">
        <Button onClick={() => void driverFor(register).openDrawer()}>Open drawer</Button>
        <Button
          onClick={async () => {
            const blob = await exportQueue()
            const a = document.createElement('a')
            a.href = URL.createObjectURL(blob)
            a.download = `offline-queue-${register.code}-${new Date().toISOString().slice(0, 10)}.json`
            a.click()
          }}
        >
          Export offline queue
        </Button>
        <Button kind="primary" onClick={() => void onRefresh().then(onClose)}>Refresh catalog</Button>
      </div>
    </Modal>
  )
}
