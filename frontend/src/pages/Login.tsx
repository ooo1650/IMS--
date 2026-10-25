import { useState } from 'react'
import { useAuth } from '../auth'
import { Icon } from '../components/Icon'
import { Button, ErrorBox, Field } from '../components/ui'
import { useOnline } from '../lib/sync'

export default function Login() {
  const { login } = useAuth()
  const online = useOnline()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await login(username.trim(), password)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="login-wrap">
      <section className="login-hero">
        <div className="brand brand-lg">
          <span className="brand-mark" aria-hidden><Icon name="layers" size={20} /></span>
          <span className="brand-text">IMS<span className="brand-sub">Inventory &amp; POS</span></span>
        </div>
        <div>
          <h1>Inventory and billing,<em>in one place.</em></h1>
          <p>Ring up sales, track stock across branches, receive purchases and keep IRD-ready tax records.</p>
          <ul className="login-features">
            <li><Icon name="cart" /> Fast POS billing</li>
            <li><Icon name="boxes" /> Real-time stock</li>
            <li><Icon name="transfer" /> Branch transfers</li>
            <li><Icon name="landmark" /> VAT &amp; IRD compliance</li>
            <li><Icon name="wifiOff" /> Works offline</li>
            <li><Icon name="shield" /> Role-based access</li>
          </ul>
        </div>
        <div className="login-foot">Your role and branches are set by an administrator.</div>
      </section>
      <div className="login-side">
        <form className="login-card" onSubmit={submit}>
          <h2>Sign in to your account</h2>
          <p className="muted">Enter your credentials to continue</p>
          {!online && (
            <div className="alert alert-warn">
              Offline. Cashiers who logged in on this device within the last 24 hours can still sign in for cash sales.
            </div>
          )}
          <ErrorBox error={error} />
          <Field label="Username">
            <span className="input-icon">
              <Icon name="user" size={16} />
              <input autoFocus autoComplete="username" placeholder="Enter your username" value={username} onChange={(e) => setUsername(e.target.value)} required />
            </span>
          </Field>
          <Field label="Password">
            <span className="input-icon">
              <Icon name="lock" size={16} />
              <input type="password" autoComplete="current-password" placeholder="Enter your password" value={password} onChange={(e) => setPassword(e.target.value)} required />
            </span>
          </Field>
          <Button kind="primary" type="submit" busy={busy} className="btn-block btn-xl">
            Sign in
          </Button>
        </form>
      </div>
    </div>
  )
}
