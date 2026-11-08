import SalesAnalytics from '../components/SalesAnalytics'
import { ErrorBox, PageHead, useLoad } from '../components/ui'
import { useAuth } from '../auth'
import { api } from '../lib/api'
import { npr } from '../lib/money'
import { AttentionList, OverviewStat, ReportTables, type BranchSummary, type ReportLists } from './Dashboard'

export default function BranchReport() {
  const { branch } = useAuth()
  const { data, error } = useLoad(() => api<ReportLists & { summary: BranchSummary }>('reports/branch'), [branch?.id])
  if (error) return <ErrorBox error={error} />
  if (!data) return <div className="splash">Loading…</div>
  const s = data.summary
  return (
    <div>
      <PageHead title={`${s.code} — ${s.name}`} sub={`Branch performance · ${s.timezone}`} />
      <div className="dash-row">
        <section className="card">
          <h2>Sales overview</h2>
          <div className="overview">
            <OverviewStat label="Today" value={npr(s.today.total_paisa)} sub={`${s.today.count} sales`} />
            <OverviewStat label="This week" value={npr(s.week.total_paisa)} sub={`${s.week.count} sales`} />
            <OverviewStat label="This month" value={npr(s.month.total_paisa)} sub={`${s.month.count} sales`} />
            <OverviewStat label="Inventory value" value={npr(s.inventory_value_paisa)} />
          </div>
        </section>
        <section className="card">
          <h2>Needs attention</h2>
          <AttentionList rows={[
            { label: 'Stock conflicts', value: s.stock_conflicts, tone: 'bad', hint: 'oversold' },
            { label: 'Negative stock', value: s.negative_stock, tone: 'bad', hint: 'recount' },
            { label: 'Sales to review', value: s.needs_review, tone: 'warn' },
            { label: 'Low stock', value: s.low_stock, tone: 'warn' },
            { label: 'Purchase orders pending', value: s.pending_pos },
          ]} />
        </section>
      </div>
      <h2 className="section-title">Sales analytics</h2>
      <SalesAnalytics key={branch?.id} />
      <h2 className="section-title">Stock</h2>
      <ReportTables data={data} />
    </div>
  )
}
