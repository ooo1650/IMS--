import { Link } from 'react-router-dom'
import { DailyBars, InlineBar } from '../components/charts'
import { Icon, type IconName } from '../components/Icon'
import { Badge, ErrorBox, PageHead, fmtDate, useLoad } from '../components/ui'
import { api } from '../lib/api'
import { TurnoverMeter } from './Compliance'
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
interface DashboardData extends ReportLists {
  totals: { today: number; week: number; month: number; inventory_value_paisa: number; stock_conflicts: number; pending_pos: number; low_stock: number }
  branches: BranchSummary[]
  fonepay_mismatches: { id: number; branch__code: string | null; prn: string; kind: string; found_at: string }[]
}

export function Tile({ label, value, sub, tone, icon, iconTone }: {
  label: string; value: string | number; sub?: string; tone?: 'bad' | 'warn'
  icon?: IconName; iconTone?: 'info' | 'warn' | 'bad' | 'violet'
}) {
  return (
    <div className={`tile ${tone ? `tile-${tone}` : ''}`}>
      {icon && <div className={`tile-icon ${iconTone ? `tone-${iconTone}` : ''}`}><Icon name={icon} size={20} /></div>}
      <div className="tile-label">{label}</div>
      <div className="tile-value">{value}</div>
      {sub && <div className="tile-sub">{sub}</div>}
    </div>
  )
}

function OverviewStat({ icon, tone, label, value }: { icon: IconName; tone?: 'info' | 'warn' | 'bad' | 'violet'; label: string; value: string | number }) {
  return (
    <div>
      <div className={`tile-icon ${tone ? `tone-${tone}` : ''}`}><Icon name={icon} size={18} /></div>
      <div className="overview-row"><strong>{value}</strong><span>{label}</span></div>
    </div>
  )
}

export function trendSeries(trend: Record<string, number | string>[]) {
  return trend.map((row) => ({
    date: String(row.date),
    value: Object.entries(row).reduce((a, [k, v]) => (k === 'date' ? a : a + Number(v)), 0),
  }))
}

export function ReportTables({ data }: { data: ReportLists }) {
  return (
    <div className="grid-2">
      <section className="card">
        <h2>Top products · 30 days</h2>
        <table className="table">
          <thead><tr><th>Product</th><th className="num">Qty</th><th className="num">Revenue</th></tr></thead>
          <tbody>
            {data.top_products.map((p) => (
              <tr key={p.product_id}><td>{p.product__name} <span className="muted small">{p.product__sku}</span></td><td className="num">{p.qty}</td><td className="num">{npr(p.revenue_paisa)}</td></tr>
            ))}
          </tbody>
        </table>
      </section>
      <section className="card">
        <h2>Low &amp; out of stock</h2>
        <table className="table">
          <thead><tr><th>Branch</th><th>Product</th><th className="num">On hand</th><th className="num">Reorder at</th></tr></thead>
          <tbody>
            {data.low_stock.slice(0, 15).map((i) => (
              <tr key={`${i.branch}-${i.sku}`}><td>{i.branch}</td><td>{i.name}</td><td className={`num ${i.on_hand <= 0 ? 'text-bad' : ''}`}>{i.on_hand}</td><td className="num">{i.reorder_point}</td></tr>
            ))}
            {!data.low_stock.length && <tr><td colSpan={4} className="empty">All stocked.</td></tr>}
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
            {!data.slow_movers.length && <tr><td colSpan={4} className="empty">Nothing stale.</td></tr>}
          </tbody>
        </table>
      </section>
      <section className="card">
        <h2>Stock conflicts to resolve</h2>
        <table className="table">
          <thead><tr><th>Branch</th><th>Receipt</th><th className="num">Total</th><th>When</th></tr></thead>
          <tbody>
            {data.stock_conflicts.map((s) => (
              <tr key={s.id}><td>{s.branch__code}</td><td><Link to={`/sales?id=${s.id}`}>{s.receipt_number}</Link> {s.offline && <Badge>offline</Badge>}</td><td className="num">{npr(s.total_paisa)}</td><td>{fmtDate(s.paid_at)}</td></tr>
            ))}
            {!data.stock_conflicts.length && <tr><td colSpan={4} className="empty">None.</td></tr>}
          </tbody>
        </table>
      </section>
    </div>
  )
}

export default function Dashboard() {
  const { data, error } = useLoad(() => api<DashboardData>('reports/dashboard'), [])
  if (error) return <ErrorBox error={error} />
  if (!data) return <div className="splash">Loading dashboard…</div>
  const maxMonth = Math.max(...data.branches.map((b) => b.month.total_paisa))
  return (
    <div>
      <PageHead title="Organization dashboard" sub={`${data.branches.length} branches · sales use each branch's local day`} />
      <div className="dash-row">
        <section className="card">
          <h2>Sales overview</h2>
          <div className="overview">
            <OverviewStat icon="coins" label="Today" value={npr(data.totals.today)} />
            <OverviewStat icon="trending" tone="info" label="This week" value={npr(data.totals.week)} />
            <OverviewStat icon="chart" tone="violet" label="This month" value={npr(data.totals.month)} />
            <OverviewStat icon="boxes" tone="warn" label="Inventory value" value={npr(data.totals.inventory_value_paisa)} />
          </div>
        </section>
        <section className="card">
          <h2>Needs attention</h2>
          <div className="overview">
            <OverviewStat icon="alert" tone="warn" label="Low stock" value={data.totals.low_stock} />
            <OverviewStat icon="truck" tone="info" label="POs pending" value={data.totals.pending_pos} />
          </div>
        </section>
      </div>
      <div className="tiles">
        <Tile icon="x" iconTone="bad" label="Stock conflicts" value={data.totals.stock_conflicts} tone={data.totals.stock_conflicts ? 'bad' : undefined} sub="sales that oversold stock" />
        <Tile icon="qr" iconTone="violet" label="FonePay mismatches" value={data.fonepay_mismatches.length} tone={data.fonepay_mismatches.length ? 'bad' : undefined} sub="reconciliation" />
        <Tile icon="store" label="Branches" value={data.branches.length} sub={`${data.branches.reduce((a, b) => a + b.needs_review, 0)} sales need review`} />
      </div>
      <section className="card">
        <DailyBars data={trendSeries(data.trend)} label="Sales per day · last 14 days" />
      </section>
      <TurnoverMeter />
      <section className="card">
        <h2>Branch comparison</h2>
        <div className="table-scroll">
          <table className="table">
            <thead>
              <tr>
                <th>Branch</th><th className="num">Today</th><th className="num">Week</th><th>Month</th>
                <th className="num">Inventory</th><th className="num">Low</th><th className="num">Neg.</th><th className="num">Conflicts</th><th className="num">Review</th>
              </tr>
            </thead>
            <tbody>
              {data.branches.map((b) => (
                <tr key={b.branch_id}>
                  <td><strong>{b.code}</strong> <span className="muted small">{b.name}</span></td>
                  <td className="num">{npr(b.today.total_paisa)}</td>
                  <td className="num">{npr(b.week.total_paisa)}</td>
                  <td><div className="bar-cell"><InlineBar value={b.month.total_paisa} max={maxMonth} /><span>{npr(b.month.total_paisa)}</span></div></td>
                  <td className="num">{npr(b.inventory_value_paisa)}</td>
                  <td className="num">{b.low_stock}</td>
                  <td className={`num ${b.negative_stock ? 'text-bad' : ''}`}>{b.negative_stock}</td>
                  <td className={`num ${b.stock_conflicts ? 'text-bad' : ''}`}>{b.stock_conflicts}</td>
                  <td className="num">{b.needs_review}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
      <ReportTables data={data} />
      {data.fonepay_mismatches.length > 0 && (
        <section className="card">
          <h2>FonePay reconciliation mismatches</h2>
          <table className="table">
            <thead><tr><th>Branch</th><th>PRN</th><th>Kind</th><th>Found</th></tr></thead>
            <tbody>
              {data.fonepay_mismatches.map((m) => (
                <tr key={m.id}><td>{m.branch__code ?? '—'}</td><td>{m.prn}</td><td><Badge tone="bad">{m.kind.replace(/_/g, ' ')}</Badge></td><td>{fmtDate(m.found_at)}</td></tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </div>
  )
}
