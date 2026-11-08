import { Link } from 'react-router-dom'
import { Badge, ErrorBox, PageHead, useLoad } from '../shared/ui'
import { api } from '../shared/api'
import type { Business } from './Businesses'

interface OverviewData {
  businesses: (Business & { cbms: { enabled: boolean; pending: number; failed: number; paused_reason: string } })[]
  mail_configured: boolean
  platform_admins: number
  test_login: boolean
  demo_seed_allowed: boolean
}

export default function Overview() {
  const { data, error } = useLoad(() => api<OverviewData>('platform/overview'), [])
  if (error) return <ErrorBox error={error} />
  if (!data) return <div className="splash">Loading…</div>
  const problems = [
    !data.mail_configured && { text: 'No mail server: sign-in and setup codes only go to the backend log', to: '/system' },
    data.test_login && { text: 'Test sign-in is on (IMS_TEST_LOGIN): anyone on the local network can sign in as anyone', to: null },
    ...data.businesses.filter((b) => b.cbms.paused_reason).map((b) => ({ text: `${b.name}: CBMS paused, ${b.cbms.paused_reason}`, to: `/businesses/${b.id}` })),
    ...data.businesses.filter((b) => b.cbms.failed).map((b) => ({ text: `${b.name}: ${b.cbms.failed} CBMS posts failed`, to: `/businesses/${b.id}` })),
  ].filter(Boolean) as { text: string; to: string | null }[]
  return (
    <div>
      <PageHead title="Overview" sub={`${data.businesses.length} business${data.businesses.length === 1 ? '' : 'es'} · ${data.platform_admins} operator account${data.platform_admins === 1 ? '' : 's'}`} />
      {problems.length > 0 && (
        <section className="card">
          <h2>Needs attention</h2>
          <ul className="attention">
            {problems.map((p) => (
              <li key={p.text} className="attention-warn">
                <span><span className="status-dot" aria-hidden />{p.text}</span>
                {p.to && <Link to={p.to}>Open</Link>}
              </li>
            ))}
          </ul>
        </section>
      )}
      <div className="biz-list">
        {data.businesses.map((b) => (
          <Link key={b.id} to={`/businesses/${b.id}`} className="card biz-card">
            <h2>{b.name}</h2>
            <span className="muted small">{[b.trade, b.pan && `PAN ${b.pan}`, b.code].filter(Boolean).join(' · ')}</span>
            <div className="biz-facts">
              <span><strong className="fig">{b.branches}</strong> branches</span>
              <span><strong className="fig">{b.users}</strong> people</span>
              <span><strong className="fig">{b.products}</strong> products</span>
            </div>
            <div className="flags">
              {!b.is_active && <Badge tone="bad">inactive</Badge>}
              {b.cbms.enabled ? <span className="muted small">CBMS on · {b.cbms.pending} pending</span> : <span className="muted small">CBMS off</span>}
            </div>
          </Link>
        ))}
      </div>
      {!data.businesses.length && <div className="card empty">No businesses yet. <Link to="/businesses">Create the first one</Link></div>}
    </div>
  )
}
