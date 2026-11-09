import { Link } from 'react-router-dom'
import { InlineBar } from '../components/charts'
import SalesAnalytics from '../components/SalesAnalytics'
import { Badge, ErrorBox, fmtDate, PageHead, PageSkeleton, useLoad } from '../components/ui'
import { Icon } from '../components/Icon'
import { api } from '../lib/api'
import { TurnoverMeter } from '../components/TurnoverMeter'
import { AttentionList, OverviewStat, ReportTables, type BranchSummary, type ReportLists } from '../components/stats'
import { npr } from '../lib/money'

interface DashboardData extends ReportLists {
  totals: { today: number; week: number; month: number; inventory_value_paisa: number; stock_conflicts: number; pending_pos: number; low_stock: number }
  branches: BranchSummary[]
  fonepay_mismatches: { id: number; branch__code: string | null; prn: string; kind: string; found_at: string }[]
}

interface SetupStatus { products: boolean; stock: boolean; suppliers: boolean; staff: boolean; sale: boolean }

const SETUP_STEPS: { key: keyof SetupStatus; title: string; why: string; to: string; action: string }[] = [
  { key: 'products', title: 'Add your products', why: 'Name, price and VAT. Give opening stock in the same form', to: '/products', action: 'Products' },
  { key: 'stock', title: 'Put stock in', why: "The till can't sell what a branch doesn't have: opening stock, Inventory → Adjust, or a purchase", to: '/inventory', action: 'Inventory' },
  { key: 'suppliers', title: 'Add a supplier', why: 'So purchases record who you bought from and the VAT you can claim', to: '/purchasing', action: 'Purchasing' },
  { key: 'staff', title: 'Add your staff', why: 'Each person gets their own login and a role per branch', to: '/admin', action: 'Users & access' },
  { key: 'sale', title: 'Make your first sale', why: 'Open a shift on a till, scan or search, take payment', to: '/pos', action: 'Point of Sale' },
]

/** First-run checklist: ticks itself off from real data, disappears when done. */
function SetupChecklist() {
  const { data } = useLoad(() => api<SetupStatus>('setup-status'), [])
  if (!data) return null
  const done = SETUP_STEPS.filter((s) => data[s.key]).length
  if (done === SETUP_STEPS.length) return null
  return (
    <section className="card setup">
      <h2>Get your shop running <span className="muted small fig">{done} of {SETUP_STEPS.length} done</span></h2>
      <ol className="setup-list">
        {SETUP_STEPS.map((s) => (
          <li key={s.key} className={data[s.key] ? 'done' : ''}>
            <span className="setup-tick" aria-label={data[s.key] ? 'Done' : 'To do'}>{data[s.key] && <Icon name="check" size={14} />}</span>
            <span><span className="setup-title">{s.title}</span><span className="setup-why">{s.why}</span></span>
            {data[s.key] ? <span /> : <Link className="btn btn-default" to={s.to}>{s.action}</Link>}
          </li>
        ))}
      </ol>
    </section>
  )
}

export default function Dashboard() {
  const { data, error } = useLoad(() => api<DashboardData>('reports/dashboard'), [])
  if (error) return <ErrorBox error={error} />
  if (!data) return <PageSkeleton tiles={4} />
  const maxMonth = Math.max(...data.branches.map((b) => b.month.total_paisa))
  return (
    <div>
      <PageHead title="Organization dashboard" sub={`${data.branches.length} branches · sales use each branch's local day`} />
      <SetupChecklist />
      <div className="dash-row">
        <section className="card">
          <h2>Sales overview</h2>
          <div className="overview">
            <OverviewStat label="Today" value={npr(data.totals.today)} />
            <OverviewStat label="This week" value={npr(data.totals.week)} />
            <OverviewStat label="This month" value={npr(data.totals.month)} />
            <OverviewStat label="Inventory value" value={npr(data.totals.inventory_value_paisa)} />
          </div>
        </section>
        <section className="card">
          <h2>Needs attention</h2>
          <AttentionList rows={[
            { label: 'Stock conflicts', value: data.totals.stock_conflicts, tone: 'bad', hint: 'oversold', to: '#stock-conflicts' },
            { label: 'FonePay mismatches', value: data.fonepay_mismatches.length, tone: 'bad', to: '#fonepay-mismatches' },
            { label: 'Sales to review', value: data.branches.reduce((a, b) => a + b.needs_review, 0), tone: 'warn', hint: 'per branch below', to: '#branch-comparison' },
            { label: 'Low stock', value: data.totals.low_stock, tone: 'warn', to: '#low-stock' },
            { label: 'Purchase orders pending', value: data.totals.pending_pos, to: '/purchasing' },
          ]} />
        </section>
      </div>
      <h2 className="section-title">Sales analytics</h2>
      <SalesAnalytics branches={data.branches.map((b) => ({ id: b.branch_id, code: b.code, name: b.name }))} />
      <h2 className="section-title">Branches &amp; stock</h2>
      <TurnoverMeter />
      <section className="card" id="branch-comparison">
        <h2>Branch comparison</h2>
        <div className="table-scroll">
          <table className="table">
            <thead>
              <tr>
                <th>Branch</th><th className="num">Today</th><th className="num">Week</th><th>Month</th>
                <th className="num">Inventory</th><th className="num">Low</th><th className="num">Negative</th><th className="num">Conflicts</th><th className="num">Review</th>
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
        <section className="card" id="fonepay-mismatches">
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
