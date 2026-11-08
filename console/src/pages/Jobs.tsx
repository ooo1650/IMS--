/** Maintenance jobs that used to need `manage.py` on the server. Each run is audited. */
import { useState } from 'react'
import { Button, ErrorBox, PageHead, fmtDate, useLoad } from '../shared/ui'
import { api } from '../shared/api'

interface Job { name: string; label: string; last_run_at: string | null; last_ok: boolean | null }

export default function Jobs() {
  const jobs = useLoad(() => api<Job[]>('platform/jobs'), [])
  const overview = useLoad(() => api<{ demo_seed_allowed: boolean; businesses: unknown[] }>('platform/overview'), [])
  const [running, setRunning] = useState<string | null>(null)
  const [result, setResult] = useState<{ name: string; ok: boolean; output: string } | null>(null)
  const run = async (name: string, url: string) => {
    setRunning(name)
    setResult(null)
    try {
      const r = await api<{ ok: boolean; output: string }>(url, { method: 'POST' })
      setResult({ name, ...r })
    } catch (e) {
      setResult({ name, ok: false, output: (e as Error).message })
    } finally {
      setRunning(null)
      jobs.reload()
    }
  }
  return (
    <div>
      <PageHead title="Jobs" sub="The scheduler runs these automatically; run one now when a client asks" />
      <ErrorBox error={jobs.error} />
      <div className="card flush">
        <table className="table">
          <thead><tr><th>Job</th><th>Last run</th><th /></tr></thead>
          <tbody>
            {jobs.data?.map((j) => (
              <tr key={j.name}>
                <td><div className="cell-strong">{j.label}</div><div className="mono muted small">{j.name}</div></td>
                <td>{j.last_run_at ? <>{fmtDate(j.last_run_at)} {j.last_ok === false && <span className="text-bad">· failed</span>}</> : <span className="muted">From the console: never</span>}</td>
                <td className="row-actions"><Button busy={running === j.name} disabled={!!running} onClick={() => void run(j.name, `platform/jobs/${j.name}`)}>Run now</Button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {result && (
        <section className="card">
          <h2>{result.name}: {result.ok ? 'done' : 'failed'}</h2>
          <pre className="job-output">{result.output || '(no output)'}</pre>
        </section>
      )}
      {overview.data?.demo_seed_allowed && overview.data.businesses.length === 0 && (
        <section className="card">
          <h2>Demo data</h2>
          <p className="card-sub">Loads two demo businesses into this empty install (demo servers only)</p>
          <Button busy={running === 'demo'} disabled={!!running} onClick={() => void run('demo', 'platform/demo-seed')}>Load demo businesses</Button>
        </section>
      )}
    </div>
  )
}
