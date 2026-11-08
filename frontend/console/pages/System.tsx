/** Deployment-wide settings that used to be environment variables. Secrets and
 *  security guard rails stay in the server's .env on purpose (listed below). */
import { useState } from 'react'
import { Button, ErrorBox, Field, PageHead, fmtDate, useLoad } from '../../src/components/ui'
import { api } from '../../src/lib/api'

interface SystemData {
  group_name: string; otp_required_roles: string[]; login_window_minutes: number; login_max_per_username: number
  login_max_per_ip: number; smtp_host: string; smtp_port: number; smtp_user: string; smtp_password_set: boolean
  smtp_use_tls: boolean; from_email: string; updated_at: string; updated_by_name: string | null
}
const ROLES = ['SuperAdmin', 'OrgAdmin', 'BranchManager', 'Cashier', 'InventoryClerk', 'Viewer']

export default function System() {
  const sys = useLoad(() => api<SystemData>('platform/system'), [])
  if (!sys.data) return <ErrorBox error={sys.error} />
  return <SystemForm initial={sys.data} onSaved={sys.reload} />
}

function SystemForm({ initial, onSaved }: { initial: SystemData; onSaved: () => void }) {
  const [f, setF] = useState({ ...initial, smtp_password: '' })
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [testTo, setTestTo] = useState('')
  const [testResult, setTestResult] = useState<{ ok: boolean; detail: string } | null>(null)
  const num = (k: keyof SystemData) => (e: React.ChangeEvent<HTMLInputElement>) => setF({ ...f, [k]: Number(e.target.value) })
  const text = (k: keyof SystemData) => (e: React.ChangeEvent<HTMLInputElement>) => setF({ ...f, [k]: e.target.value })
  const saveAll = async () => {
    setBusy(true)
    setError(null)
    setNotice(null)
    try {
      const body: Record<string, unknown> = { ...f }
      if (!f.smtp_password) delete body.smtp_password
      for (const k of ['updated_at', 'updated_by_name', 'smtp_password_set']) delete body[k]
      await api('platform/system', { method: 'PATCH', body })
      setNotice('Saved')
      onSaved()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <div>
      <PageHead title="System settings" sub={`For every business · last changed ${fmtDate(initial.updated_at)}${initial.updated_by_name ? ` by ${initial.updated_by_name}` : ''}`}>
        <Button kind="primary" busy={busy} onClick={() => void saveAll()}>Save</Button>
      </PageHead>
      <ErrorBox error={error} />
      {notice && <div className="alert alert-good">{notice}</div>}

      <section className="card">
        <h2>Email</h2>
        <p className="card-sub">Sign-in, password reset and setup codes. With no server set, codes are written to the backend log</p>
        <div className="form-grid">
          <Field label="SMTP server"><input className="mono" placeholder="smtp-relay.brevo.com" value={f.smtp_host} onChange={text('smtp_host')} /></Field>
          <Field label="Port"><input inputMode="numeric" value={f.smtp_port} onChange={num('smtp_port')} /></Field>
          <Field label="Username"><input autoComplete="off" value={f.smtp_user} onChange={text('smtp_user')} /></Field>
          <Field label="Password" hint={initial.smtp_password_set ? 'Saved (encrypted). Leave blank to keep it' : 'Not set'}>
            <input type="password" autoComplete="new-password" value={f.smtp_password} onChange={text('smtp_password' as keyof SystemData)} />
          </Field>
          <Field label="Send as" hint='e.g. Himalaya Group <no-reply@example.com>'><input value={f.from_email} onChange={text('from_email')} /></Field>
        </div>
        <label className="check"><input type="checkbox" checked={f.smtp_use_tls} onChange={(e) => setF({ ...f, smtp_use_tls: e.target.checked })} /> Use STARTTLS (port 587)</label>
        <div className="inline-form">
          <input type="email" placeholder="Send a test to" value={testTo} onChange={(e) => setTestTo(e.target.value)} />
          <Button onClick={async () => {
            setTestResult(null)
            try {
              setTestResult(await api<{ ok: boolean; detail: string }>('platform/system/test-email', { body: { to: testTo } }))
            } catch (e) {
              setTestResult({ ok: false, detail: (e as Error).message })
            }
          }}>Send test email</Button>
        </div>
        <p className="muted small">The test uses the saved settings: save first</p>
        {testResult && <div className={`alert ${testResult.ok ? 'alert-good' : 'alert-error'}`}>{testResult.detail}</div>}
      </section>

      <section className="card">
        <h2>Sign-in</h2>
        <Field label="Group name" hint="Shown on the sign-in page above every business"><input value={f.group_name} onChange={text('group_name')} /></Field>
        <h3>Roles that must enter an emailed code</h3>
        <div className="check-grid">
          {ROLES.map((r) => (
            <label key={r} className="check">
              <input type="checkbox" checked={f.otp_required_roles.includes(r)}
                onChange={(e) => setF({ ...f, otp_required_roles: e.target.checked ? [...f.otp_required_roles, r] : f.otp_required_roles.filter((x) => x !== r) })} />
              {r.replace(/([a-z])([A-Z])/g, '$1 $2')}
            </label>
          ))}
        </div>
        <p className="muted small">Operators always need a code, whatever is ticked here</p>
        <h3>Lockout after failed sign-ins</h3>
        <div className="form-grid">
          <Field label="Window (minutes)"><input inputMode="numeric" value={f.login_window_minutes} onChange={num('login_window_minutes')} /></Field>
          <Field label="Failures per account"><input inputMode="numeric" value={f.login_max_per_username} onChange={num('login_max_per_username')} /></Field>
          <Field label="Failures per network address"><input inputMode="numeric" value={f.login_max_per_ip} onChange={num('login_max_per_ip')} /></Field>
        </div>
      </section>

      <section className="card">
        <h2>Kept in the server's .env</h2>
        <p className="card-sub">Needed before the database can be read, or security limits an account must not be able to loosen</p>
        <ul className="muted small">
          <li>Database passwords, DJANGO_SECRET_KEY and CBMS_ENCRYPTION_KEY (encrypts the passwords saved here)</li>
          <li>IMS_DOMAIN, allowed hosts, HTTPS and trusted proxies; IMS_CONSOLE_ALLOW (who may open this console)</li>
          <li>IMS_TEST_LOGIN, IMS_ALLOW_FONEPAY_MOCK, IMS_FONEPAY_HOSTS, IMS_ALLOW_DEMO_SEED</li>
        </ul>
      </section>
    </div>
  )
}
