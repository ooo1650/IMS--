/**
 * Number cards and stock/attention lists shared by the dashboard, the branch
 * report and Inventory. They live here, not in a page, so importing a tile
 * doesn't pull a whole other screen into the download.
 */
import { Link } from 'react-router-dom'
import { Badge, fmtDate } from './ui'
import { Icon } from './Icon'
import { npr } from '../lib/money'

interface Period { total_paisa: number; count: number }
export interface BranchSummary {
  branch_id: number; code: string; name: string; timezone: string
  today: Period; week: Period; month: Period
  inventory_value_paisa: number; low_stock: number; out_of_stock: number; negative_stock: number
  stock_conflicts: number; needs_review: number; pending_pos: number
}
export interface ReportLists {
  trend: Record<string, number | string>[]
  top_products: { product_id: number; product__sku: string; product__name: string; qty: number; revenue_paisa: number }[]
  slow_movers: { branch: string; sku: string; name: string; on_hand: number; value_paisa: number }[]
  low_stock: { branch: string; sku: string; name: string; on_hand: number; reorder_point: number; reorder_qty: number }[]
  stock_conflicts: { id: number; branch__code: string; receipt_number: string; total_paisa: number; paid_at: string; offline: boolean }[]
}

/** A number on a card. With `onClick` it is a button that opens the items
 *  behind the number (e.g. "Low stock 3" -> the three products); `action`
 *  says what clicking does, and `active` marks the tile whose list is shown. */
export function Tile({ label, value, sub, tone, onClick, action, active }: {
  label: string; value: string | number; sub?: string; tone?: 'bad' | 'warn'
  onClick?: () => void; action?: string; active?: boolean
}) {
  const body = (
    <>
      <div className="tile-label">{label}</div>
      <div className="tile-value">{value}</div>
      {sub && <div className="tile-sub">{sub}</div>}
      {onClick && action && <div className="tile-action">{action} <Icon name="chevron-right" size={14} /></div>}
    </>
  )
  const cls = `tile ${tone ? `tile-${tone}` : ''} ${active ? 'tile-active' : ''}`
  return onClick
    ? <button type="button" className={`${cls} tile-button`} aria-pressed={active} onClick={onClick}>{body}</button>
    : <div className={cls}>{body}</div>
}

export function OverviewStat({ label, value, sub }: { label: string; value: string | number; sub?: string }) {
  return (
    <div>
      <div className="overview-row"><strong>{value}</strong><span>{label}</span></div>
      {sub && <span className="muted small">{sub}</span>}
    </div>
  )
}

/** Exceptions as a quiet list: a number per row, and a status dot only when it needs action. */
/** Counts that need someone. A row with something in it and a `to` links to
 *  the list where it gets dealt with (a router path, or "#id" on this page). */
export function AttentionList({ rows }: { rows: { label: string; value: number; tone?: 'warn' | 'bad'; hint?: string; to?: string }[] }) {
  return (
    <ul className="attention">
      {rows.map((r) => {
        const flagged = r.value > 0 && r.tone
        const inner = (
          <>
            <span>{flagged && <span className="status-dot" aria-hidden />}{r.label}{r.hint && <span className="muted small"> · {r.hint}</span>}</span>
            <strong className="fig">{r.value}{r.value > 0 && r.to && <Icon name="chevron-right" size={14} />}</strong>
          </>
        )
        return (
          <li key={r.label} className={flagged ? `attention-${r.tone}` : ''}>
            {r.value > 0 && r.to
              ? r.to.startsWith('#')
                ? <a className="attention-link" href={r.to}>{inner}</a>
                : <Link className="attention-link" to={r.to}>{inner}</Link>
              : inner}
          </li>
        )
      })}
    </ul>
  )
}

/** `branchLinks`: the lists are this branch's, so they can link to its Inventory and Sales screens. */
export function ReportTables({ data, branchLinks }: { data: ReportLists; branchLinks?: boolean }) {
  return (
    <div className="grid-2">
      <section className="card" id="low-stock">
        <h2>Low &amp; out of stock {branchLinks && data.low_stock.length > 0 && <Link className="small h2-link" to="/inventory?filter=low">Order or restock in Inventory</Link>}</h2>
        <table className="table">
          <thead><tr><th>Branch</th><th>Product</th><th className="num">On hand</th><th className="num">Reorder at</th></tr></thead>
          <tbody>
            {data.low_stock.slice(0, 15).map((i) => (
              <tr key={`${i.branch}-${i.sku}`}><td>{i.branch}</td><td>{i.name}</td><td className={`num ${i.on_hand <= 0 ? 'text-bad' : ''}`}>{i.on_hand}</td><td className="num">{i.reorder_point}</td></tr>
            ))}
            {!data.low_stock.length && <tr><td colSpan={4} className="empty">All stocked</td></tr>}
          </tbody>
        </table>
      </section>
      <section className="card">
        <h2>Slow movers · no sales in 30 days</h2>
        <table className="table">
          <thead><tr><th>Branch</th><th>Product</th><th className="num">On hand</th><th className="num">Value</th></tr></thead>
          <tbody>
            {data.slow_movers.map((i) => (
              <tr key={`${i.branch}-${i.sku}`}><td>{i.branch}</td><td>{i.name}</td><td className="num">{i.on_hand}</td><td className="num">{npr(i.value_paisa)}</td></tr>
            ))}
            {!data.slow_movers.length && <tr><td colSpan={4} className="empty">Nothing stale</td></tr>}
          </tbody>
        </table>
      </section>
      <section className="card" id="stock-conflicts">
        <h2>Stock conflicts to resolve {branchLinks && data.stock_conflicts.length > 0 && <Link className="small h2-link" to="/sales?filter=conflicts">Resolve in Sales</Link>}</h2>
        <table className="table">
          <thead><tr><th>Branch</th><th>Receipt</th><th className="num">Total</th><th>When</th></tr></thead>
          <tbody>
            {data.stock_conflicts.map((s) => (
              <tr key={s.id}><td>{s.branch__code}</td><td><Link to={`/sales?id=${s.id}`}>{s.receipt_number}</Link> {s.offline && <Badge>offline</Badge>}</td><td className="num">{npr(s.total_paisa)}</td><td>{fmtDate(s.paid_at)}</td></tr>
            ))}
            {!data.stock_conflicts.length && <tr><td colSpan={4} className="empty">None</td></tr>}
          </tbody>
        </table>
      </section>
    </div>
  )
}
