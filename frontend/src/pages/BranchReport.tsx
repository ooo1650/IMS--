import { DailyBars } from '../components/charts'
import { ErrorBox, PageHead, useLoad } from '../components/ui'
import { useAuth } from '../auth'
import { api } from '../lib/api'
import { npr } from '../lib/money'
import { ReportTables, Tile, trendSeries, type BranchSummary, type ReportLists } from './Dashboard'

export default function BranchReport() {
  const { branch } = useAuth()
  const { data, error } = useLoad(() => api<ReportLists & { summary: BranchSummary }>('reports/branch'), [branch?.id])
  if (error) return <ErrorBox error={error} />
  if (!data) return <div className="splash">Loading…</div>
  const s = data.summary
  return (
    <div>
      <PageHead title={`${s.code} — ${s.name}`} sub={`Branch performance · ${s.timezone}`} />
      <div className="tiles">
        <Tile icon="coins" label="Sales today" value={npr(s.today.total_paisa)} sub={`${s.today.count} sales`} />
        <Tile icon="trending" iconTone="info" label="This week" value={npr(s.week.total_paisa)} sub={`${s.week.count} sales`} />
        <Tile icon="chart" iconTone="violet" label="This month" value={npr(s.month.total_paisa)} sub={`${s.month.count} sales`} />
        <Tile icon="boxes" iconTone="warn" label="Inventory value" value={npr(s.inventory_value_paisa)} />
        <Tile icon="alert" iconTone="warn" label="Low stock" value={s.low_stock} tone={s.low_stock ? 'warn' : undefined} />
        <Tile icon="minus" iconTone="bad" label="Negative stock" value={s.negative_stock} sub="needs recount" tone={s.negative_stock ? 'bad' : undefined} />
        <Tile icon="x" iconTone="bad" label="Stock conflicts" value={s.stock_conflicts} tone={s.stock_conflicts ? 'bad' : undefined} />
        <Tile icon="clipboard" iconTone="info" label="Needs review" value={s.needs_review} tone={s.needs_review ? 'warn' : undefined} />
      </div>
      <section className="card">
        <DailyBars data={trendSeries(data.trend)} label="Sales per day · last 14 days" />
      </section>
      <ReportTables data={data} />
    </div>
  )
}
