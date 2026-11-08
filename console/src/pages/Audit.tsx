import { useState } from 'react'
import { ErrorBox, LoadMore, PageHead, fmtDate, useLoad, usePaged } from '../shared/ui'
import { api, qs } from '../shared/api'
import type { Business } from './Businesses'

interface Entry { id: number; at: string; user_name: string | null; business: string | null; branch_code: string | null; action: string; object_type: string; object_id: string; data: Record<string, unknown>; ip: string | null }

export default function Audit() {
  const [action, setAction] = useState('')
  const [user, setUser] = useState('')
  const [business, setBusiness] = useState('')
  const businesses = useLoad(() => api<Business[]>('platform/businesses'), [])
  const { items, loading, error, more } = usePaged<Entry>(`platform/audit${qs({ action, user, business })}`)
  return (
    <div>
      <PageHead title="Audit log" sub="Every business and every operator action" />
      <div className="toolbar">
        <input className="search" placeholder="Action, e.g. platform. or sale." value={action} onChange={(e) => setAction(e.target.value)} />
        <input className="search" placeholder="User" value={user} onChange={(e) => setUser(e.target.value)} />
        <select value={business} onChange={(e) => setBusiness(e.target.value)} aria-label="Business">
          <option value="">All businesses</option>
          {businesses.data?.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
        </select>
      </div>
      <ErrorBox error={error} />
      <div className="card flush table-scroll">
        <table className="table">
          <thead><tr><th>When</th><th>User</th><th>Where</th><th>Action</th><th>Object</th><th>Details</th></tr></thead>
          <tbody>
            {items.map((a) => (
              <tr key={a.id}>
                <td>{fmtDate(a.at)}</td>
                <td>{a.user_name ?? <span className="muted">system</span>}</td>
                <td>{a.business ? <>{a.business} <span className="mono muted">{a.branch_code}</span></> : <span className="muted">–</span>}</td>
                <td className="mono small">{a.action}</td>
                <td className="small">{a.object_type} {a.object_id}</td>
                <td className="mono small details">{Object.keys(a.data).length ? JSON.stringify(a.data) : ''}</td>
              </tr>
            ))}
            {!items.length && !loading && <tr><td colSpan={6} className="empty">Nothing matches</td></tr>}
          </tbody>
        </table>
        <LoadMore more={more} loading={loading} />
      </div>
    </div>
  )
}
