/**
 * Sales analytics block for the Dashboard (org) and Branch report.
 * One filter row (period, and branch for org users) scopes every number below;
 * everything is compared with the previous period of the same length.
 */
import { useState } from 'react'
import { api, qs } from '../lib/api'
import { npr } from '../lib/money'
import { ErrorBox, useLoad } from './ui'
import {
  Delta, Heatmap, RankedBars, SegmentedBar, StatTile, TableView, TrendChart, count, heatmapRows, longDate, nprCompact,
  pct, type HeatCell, type Segment,
} from './analytics'

interface Totals {
  revenue_paisa: number; transactions: number; items: number; avg_basket_paisa: number; net_sales_paisa: number
  cost_paisa: number; gross_profit_paisa: number; margin_bp: number | null; discount_paisa: number
  returns_paisa: number; returns_count: number
}
interface Analytics {
  days: number; branch_codes: string[]
  current: Totals; previous: Totals
  daily: { date: string; revenue_paisa: number; transactions: number; profit_paisa: number; previous_revenue_paisa: number; branches: Record<string, number> }[]
  heatmap: HeatCell[]
  payment_mix: { method: string; amount_paisa: number; count: number }[]
  categories: { category: string; revenue_paisa: number; net_paisa: number; profit_paisa: number; margin_bp: number | null; other?: boolean }[]
  top_products: { product_id: number; sku: string; name: string; qty: number; revenue_paisa: number; profit_paisa: number; margin_bp: number | null }[]
  branches: { code: string; name: string; revenue_paisa: number; previous_revenue_paisa: number; transactions: number; avg_basket_paisa: number; margin_bp: number | null; gross_profit_paisa: number }[]
}

const PERIODS = [7, 30, 90] as const
// Fixed categorical slot per method: a method keeps its colour whatever else is present.
const METHODS: Record<string, [string, number]> = {
  cash: ['Cash', 1], fonepay: ['FonePay QR', 2], card: ['Card', 3], wallet: ['Wallet', 4], bank: ['Bank transfer', 5],
}
type Metric = 'revenue' | 'profit' | 'transactions'

export default function SalesAnalytics({ branches }: { branches?: { id: number; code: string; name: string }[] }) {
  const [days, setDays] = useState<number>(30)
  const [branch, setBranch] = useState('all')
  const [metric, setMetric] = useState<Metric>('revenue')
  const { data, error, loading } = useLoad(
    () => api<Analytics>(`reports/analytics${qs({ days: String(days), branch: branches ? branch : '' })}`), [days, branch])

  const prevLabel = `previous ${days} days`
  return (
    <div className="analytics">
      <div className="filter-row" role="group" aria-label="Report filters">
        <div className="chips" role="radiogroup" aria-label="Period">
          {PERIODS.map((d) => (
            <button key={d} role="radio" aria-checked={days === d} className={`chip ${days === d ? 'active' : ''}`} onClick={() => setDays(d)}>
              Last {d} days
            </button>
          ))}
        </div>
        {branches && branches.length > 1 && (
          <select value={branch} onChange={(e) => setBranch(e.target.value)} aria-label="Branch">
            <option value="all">All branches</option>
            {branches.map((b) => <option key={b.id} value={String(b.id)}>{b.code} — {b.name}</option>)}
          </select>
        )}
        <span className="muted small">Compared with the {prevLabel} · today included · each branch's local time</span>
      </div>
      <ErrorBox error={error} />
      {!data ? <div className="splash">Loading analytics…</div> : (
        <div className={loading ? 'refetching' : undefined} aria-busy={loading}>
          <Body data={data} metric={metric} setMetric={setMetric} prevLabel={prevLabel} />
        </div>
      )}
    </div>
  )
}

function Body({ data, metric, setMetric, prevLabel }: { data: Analytics; metric: Metric; setMetric: (m: Metric) => void; prevLabel: string }) {
  const { current: c, previous: p, daily } = data
  const metricDef = {
    revenue: { label: 'Revenue', value: (d: Analytics['daily'][0]) => d.revenue_paisa, fmt: npr, axis: nprCompact },
    profit: { label: 'Gross profit', value: (d: Analytics['daily'][0]) => d.profit_paisa, fmt: npr, axis: nprCompact },
    transactions: { label: 'Sales', value: (d: Analytics['daily'][0]) => d.transactions, fmt: (v: number) => `${count(v)} sales`, axis: count },
  }[metric]
  const points = daily.map((d) => ({
    date: d.date, value: metricDef.value(d), previous: metric === 'revenue' ? d.previous_revenue_paisa : undefined,
    extra: [
      ...(metric !== 'transactions' ? [['sales', count(d.transactions)] as [string, string]] : []),
      ...(metric !== 'revenue' ? [['revenue', npr(d.revenue_paisa)] as [string, string]] : []),
    ],
  }))
  const best = daily.reduce((a, d) => (d.revenue_paisa > a.revenue_paisa ? d : a), daily[0])
  const mix: Segment[] = Object.entries(METHODS).map(([k, [label, slot]]) => ({
    key: k, label, slot, value: data.payment_mix.find((m) => m.method === k)?.amount_paisa ?? 0,
  }))
  const mixTotal = mix.reduce((a, m) => a + m.value, 0)
  const multiBranch = data.branches.length > 1

  return (
    <>
      <div className="stats">
        <StatTile label="Revenue" value={npr(c.revenue_paisa)}
          delta={<Delta now={c.revenue_paisa} before={p.revenue_paisa} label={prevLabel} />} />
        <StatTile label="Sales" value={count(c.transactions)}
          sub={`${count(c.items)} items sold`}
          delta={<Delta now={c.transactions} before={p.transactions} label={prevLabel} />} />
        <StatTile label="Average sale" value={npr(c.avg_basket_paisa)}
          delta={<Delta now={c.avg_basket_paisa} before={p.avg_basket_paisa} label={prevLabel} />} />
        <StatTile label="Gross profit" value={npr(c.gross_profit_paisa)}
          sub={`${pct(c.margin_bp)} margin on ${nprCompact(c.net_sales_paisa)} net sales`}
          delta={<Delta now={c.gross_profit_paisa} before={p.gross_profit_paisa} label={prevLabel} />} />
        <StatTile label="Returns" value={npr(c.returns_paisa)}
          sub={`${count(c.returns_count)} credit notes · discounts ${nprCompact(c.discount_paisa)}`}
          delta={<Delta now={c.returns_paisa} before={p.returns_paisa} upIsGood={false} label={prevLabel} />} />
      </div>

      <section className="card">
        <div className="card-head">
          <div>
            <h2>{metricDef.label} per day</h2>
            <p className="card-sub">
              {best && best.revenue_paisa > 0 ? <>Best day: {longDate(best.date)} · {npr(best.revenue_paisa)}</> : 'No sales in this period yet.'}
            </p>
          </div>
          <div className="chips" role="radiogroup" aria-label="Measure">
            {(['revenue', 'profit', 'transactions'] as Metric[]).map((m) => (
              <button key={m} role="radio" aria-checked={metric === m} className={`chip ${metric === m ? 'active' : ''}`} onClick={() => setMetric(m)}>
                {{ revenue: 'Revenue', profit: 'Gross profit', transactions: 'Sales' }[m]}
              </button>
            ))}
          </div>
        </div>
        <TrendChart points={points} format={metricDef.fmt} axisFormat={metricDef.axis} title={`${metricDef.label} per day`}
          seriesLabel="This period" previousLabel={metric === 'revenue' ? `Same day, ${prevLabel}` : undefined} />
        <TableView>
          <table className="table">
            <thead><tr><th>Date</th><th className="num">Revenue</th><th className="num">Previous period</th><th className="num">Sales</th><th className="num">Gross profit</th>
              {multiBranch && data.branch_codes.map((b) => <th key={b} className="num">{b}</th>)}</tr></thead>
            <tbody>
              {daily.map((d) => (
                <tr key={d.date}><td>{longDate(d.date)}</td><td className="num">{npr(d.revenue_paisa)}</td><td className="num">{npr(d.previous_revenue_paisa)}</td>
                  <td className="num">{count(d.transactions)}</td><td className="num">{npr(d.profit_paisa)}</td>
                  {multiBranch && data.branch_codes.map((b) => <td key={b} className="num">{npr(d.branches[b] ?? 0)}</td>)}</tr>
              ))}
            </tbody>
          </table>
        </TableView>
      </section>

      <div className="grid-2">
        {multiBranch && (
          <section className="card">
            <h2>Revenue by branch</h2>
            <p className="card-sub">Ranked · change vs the {prevLabel}</p>
            <RankedBars label="Revenue by branch" rows={data.branches.map((b) => ({
              key: b.code, label: b.code, sub: `${count(b.transactions)} sales · ${pct(b.margin_bp)} margin`,
              value: b.revenue_paisa, display: nprCompact(b.revenue_paisa),
              note: <Delta now={b.revenue_paisa} before={b.previous_revenue_paisa} label={prevLabel} />,
            }))} />
          </section>
        )}
        <section className="card">
          <h2>Revenue by category</h2>
          <p className="card-sub">Ranked · gross margin on the right</p>
          <RankedBars label="Revenue by category" rows={data.categories.map((cat) => ({
            key: cat.category, label: cat.category, value: cat.revenue_paisa, display: nprCompact(cat.revenue_paisa),
            note: <span className="muted small">{pct(cat.margin_bp)}</span>,
          }))} />
        </section>
        <section className="card">
          <h2>How customers paid</h2>
          <p className="card-sub">Share of {npr(mixTotal)} collected</p>
          <SegmentedBar parts={mix} format={npr} />
          <table className="table compact">
            <thead><tr><th>Method</th><th className="num">Amount</th><th className="num">Payments</th><th className="num">Share</th></tr></thead>
            <tbody>
              {data.payment_mix.map((m) => (
                <tr key={m.method}><td>{METHODS[m.method]?.[0] ?? m.method}</td><td className="num">{npr(m.amount_paisa)}</td>
                  <td className="num">{count(m.count)}</td><td className="num">{mixTotal ? `${((m.amount_paisa / mixTotal) * 100).toFixed(1)}%` : '—'}</td></tr>
              ))}
              {!data.payment_mix.length && <tr><td colSpan={4} className="empty">No payments</td></tr>}
            </tbody>
          </table>
        </section>
      </div>

      <section className="card">
        <h2>Busiest times</h2>
        <p className="card-sub">Number of sales by weekday and hour: plan staff and tills around the dark cells</p>
        <Heatmap cells={data.heatmap} />
        <TableView>
          <table className="table">
            <thead><tr><th>Day</th><th className="num">Sales</th><th className="num">Revenue</th><th>Busiest hour</th></tr></thead>
            <tbody>{heatmapRows(data.heatmap).map((r) => (
              <tr key={r.day}><td>{r.day}</td><td className="num">{count(r.transactions)}</td><td className="num">{npr(r.revenue)}</td><td>{r.busiest}</td></tr>
            ))}</tbody>
          </table>
        </TableView>
      </section>

      <section className="card">
        <h2>Top products</h2>
        <p className="card-sub">By revenue · margin is gross profit over net sales (before VAT)</p>
        <div className="table-scroll">
          <table className="table">
            <thead><tr><th>Product</th><th className="num">Qty</th><th>Revenue</th><th className="num">Gross profit</th><th className="num">Margin</th></tr></thead>
            <tbody>
              {data.top_products.map((t) => {
                const max = data.top_products[0]?.revenue_paisa || 1
                return (
                  <tr key={t.product_id}>
                    <td>{t.name} <span className="muted small">{t.sku}</span></td>
                    <td className="num">{count(t.qty)}</td>
                    <td><div className="bar-cell"><span className="inline-bar" aria-hidden><span style={{ width: `${Math.max(2, (t.revenue_paisa / max) * 100)}%` }} /></span><span>{npr(t.revenue_paisa)}</span></div></td>
                    <td className="num">{npr(t.profit_paisa)}</td>
                    <td className={`num ${t.margin_bp != null && t.margin_bp < 0 ? 'text-bad' : ''}`}>{pct(t.margin_bp)}</td>
                  </tr>
                )
              })}
              {!data.top_products.length && <tr><td colSpan={5} className="empty">No sales in this period</td></tr>}
            </tbody>
          </table>
        </div>
      </section>
    </>
  )
}
