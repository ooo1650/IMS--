/** Barcode + price labels: A4 sticker sheets or a thermal label printer, via the print dialog. */
import { useState } from 'react'
import { barcodeSvg, labelBarWidth, symbolModules } from '../lib/barcode'
import { npr } from '../lib/money'
import { printHtml } from '../lib/printer'
import type { Product } from '../lib/types'
import { Button, ErrorBox, Field, Modal, QtyInput } from './ui'

interface Layout {
  name: string
  w: number; h: number // label size, mm
  sheet?: { cols: number; rows: number; top: number; left: number; gapX: number; gapY: number }
}

const LAYOUTS: Record<string, Layout> = {
  'a4-65': { name: 'A4 sheet · 65 labels (38.1 × 21.2 mm)', w: 38.1, h: 21.2, sheet: { cols: 5, rows: 13, top: 10.7, left: 4.65, gapX: 2.5, gapY: 0 } },
  'a4-24': { name: 'A4 sheet · 24 labels (70 × 37 mm)', w: 70, h: 37, sheet: { cols: 3, rows: 8, top: 0.5, left: 0, gapX: 0, gapY: 0 } },
  'roll-50x25': { name: 'Label printer roll · 50 × 25 mm', w: 50, h: 25 },
  'roll-38x25': { name: 'Label printer roll · 38 × 25 mm', w: 38, h: 25 },
}

const esc = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')

type LabelProduct = Pick<Product, 'id' | 'sku' | 'name' | 'barcodes' | 'sale_price_paisa' | 'branch_price_paisa' | 'tax_inclusive'>

/** Below this (mm) many shop scanners can't tell the bars apart. */
const THIN_BAR = 0.19

const codeOf = (p: LabelProduct) => p.barcodes[0]?.code || p.sku

/** Narrowest bar for this code on this label, or null when it can't be bars. */
function barWidth(code: string, L: Layout): number | null {
  try {
    return labelBarWidth(symbolModules(code), L.w, !L.sheet)
  } catch {
    return null // not encodable (non-ASCII SKU): text only
  }
}

function labelHtml(p: LabelProduct, L: Layout, opts: { price: boolean; name: boolean; small: boolean }) {
  const code = codeOf(p)
  const x = barWidth(code, L)
  const svg = x == null ? '' : barcodeSvg(code, { showText: false, width: `${+(x * symbolModules(code)).toFixed(3)}mm` })
  const price = p.branch_price_paisa ?? p.sale_price_paisa
  return `<div class="label${opts.small ? ' small' : ''}">
    ${opts.name ? `<div class="name">${esc(p.name)}</div>` : ''}
    ${svg ? `<div class="bc">${svg}</div><div class="digits">${esc(code)}</div>` : `<div class="sku">${esc(code)}</div>`}
    ${opts.price ? `<div class="price">${esc(npr(price))}${p.tax_inclusive ? '' : ' <small>+VAT</small>'}</div>` : ''}
  </div>`
}

function labelsDocument(items: { product: LabelProduct; copies: number }[], layoutKey: string, opts: { price: boolean; name: boolean; skip: number }) {
  const L = LAYOUTS[layoutKey]
  const small = L.w < 45
  const labels = items.flatMap(({ product, copies }) => Array.from({ length: copies }, () => labelHtml(product, L, { ...opts, small })))
  const css = `*{box-sizing:border-box}body{margin:0;font-family:Arial,Helvetica,sans-serif;color:#000}
    .label{width:${L.w}mm;height:${L.h}mm;padding:1mm .5mm;overflow:hidden;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:.4mm;text-align:center}
    .name{font-size:${small ? 6.5 : 8.5}pt;line-height:1.1;font-weight:600;overflow:hidden;width:100%;flex:none;${L.h < 30 ? 'white-space:nowrap;text-overflow:ellipsis' : 'max-height:2.3em'}}
    .bc{width:100%;flex:1;min-height:0;display:flex;justify-content:center}.bc svg{height:100%;flex:none}
    .digits{flex:none;font:${small ? 6 : 7.5}pt/1 monospace}
    .price{flex:none;line-height:1;font-size:${small ? 8 : 11}pt;font-weight:700}.price small{font-size:6pt;font-weight:400}
    .sku{font:${small ? 7 : 9}pt monospace}
    .page{position:relative;page-break-after:always}`
  if (!L.sheet) {
    return `<!doctype html><meta charset="utf-8"><title>Labels</title><style>${css}@page{size:${L.w}mm ${L.h}mm;margin:0}
      .label{page-break-after:always}</style><body>${labels.join('')}</body>`
  }
  const s = L.sheet
  const per = s.cols * s.rows
  const cells = [...Array<string>(opts.skip).fill('<div class="label"></div>'), ...labels]
  const pages: string[] = []
  for (let i = 0; i < cells.length; i += per) pages.push(cells.slice(i, i + per).join(''))
  return `<!doctype html><meta charset="utf-8"><title>Labels</title><style>${css}@page{size:A4;margin:0}
    .page{width:210mm;height:297mm;padding:${s.top}mm 0 0 ${s.left}mm;display:grid;grid-template-columns:repeat(${s.cols},${L.w}mm);grid-auto-rows:${L.h}mm;column-gap:${s.gapX}mm;row-gap:${s.gapY}mm;align-content:start}
    </style><body>${pages.map((p) => `<div class="page">${p}</div>`).join('')}</body>`
}

export function LabelPrint({ products, onClose }: { products: LabelProduct[]; onClose: () => void }) {
  const [layout, setLayout] = useState('a4-65')
  const [copies, setCopies] = useState<Record<number, number>>(Object.fromEntries(products.map((p) => [p.id, 1])))
  const [price, setPrice] = useState(true)
  const [name, setName] = useState(true)
  const [skip, setSkip] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const total = products.reduce((a, p) => a + (copies[p.id] ?? 0), 0)
  const sheet = LAYOUTS[layout].sheet
  const preview = products[0]
  const thin = products.filter((p) => (copies[p.id] ?? 0) > 0 && (barWidth(codeOf(p), LAYOUTS[layout]) ?? 1) < THIN_BAR)

  return (
    <Modal title={`Print labels · ${products.length} product${products.length === 1 ? '' : 's'}`} onClose={onClose} wide>
      <ErrorBox error={error} />
      <div className="label-setup">
        <div>
          <Field label="Label stock">
            <select value={layout} onChange={(e) => setLayout(e.target.value)}>
              {Object.entries(LAYOUTS).map(([k, l]) => <option key={k} value={k}>{l.name}</option>)}
            </select>
          </Field>
          {sheet && (
            <Field label="Skip used labels" hint="Start part-way through a sheet that already has labels peeled off">
              <QtyInput className="" min={0} max={sheet.cols * sheet.rows - 1} value={skip} onChange={setSkip} />
            </Field>
          )}
          <label className="check"><input type="checkbox" checked={name} onChange={(e) => setName(e.target.checked)} /> Product name</label>
          <label className="check"><input type="checkbox" checked={price} onChange={(e) => setPrice(e.target.checked)} /> Price (branch price when set)</label>
          {preview && (
            <div className="label-preview" style={{ aspectRatio: `${LAYOUTS[layout].w} / ${LAYOUTS[layout].h}` }}
              dangerouslySetInnerHTML={{ __html: labelHtml(preview, LAYOUTS[layout], { price, name, small: false }) }} />
          )}
        </div>
        <div className="table-scroll tall">
          <table className="table">
            <thead><tr><th>Product</th><th>Barcode</th><th className="num">Copies</th></tr></thead>
            <tbody>
              {products.map((p) => (
                <tr key={p.id}>
                  <td>{p.name}</td>
                  <td className="mono small">{p.barcodes[0]?.code ?? <span className="muted">SKU {p.sku}</span>}</td>
                  <td className="num">
                    <QtyInput min={0} max={1000} aria-label={`Copies of ${p.name}`} value={copies[p.id] ?? 0}
                      onChange={(n) => setCopies({ ...copies, [p.id]: n })} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      {thin.length > 0 && (
        <div className="alert alert-warn small">
          The code is long for this label, so the bars will be very thin and may not scan: {thin.map((p) => p.name).join(', ')}.
          Choose a wider label, or give the product a 13-digit code (edit → “Make one”).
        </div>
      )}
      <p className="muted small">In the print dialog choose the label printer (or A4 printer), set margins to <strong>None</strong> and scale to <strong>100%</strong>.</p>
      <div className="modal-foot">
        <Button onClick={onClose}>Cancel</Button>
        <Button kind="primary" disabled={!total} why="Set how many labels to print for at least one product" onClick={async () => {
          setError(null)
          try {
            const items = products.map((p) => ({ product: p, copies: copies[p.id] ?? 0 })).filter((i) => i.copies > 0)
            await printHtml(labelsDocument(items, layout, { price, name, skip: sheet ? skip : 0 }), 'Labels')
          } catch (e) {
            setError((e as Error).message)
          }
        }}>Print {total} label{total === 1 ? '' : 's'}</Button>
      </div>
    </Modal>
  )
}
