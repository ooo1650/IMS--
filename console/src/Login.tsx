/** Operator sign-in: password, then the emailed code (always required for
 *  operators). No sign-up and no offline mode: operator accounts are created
 *  with `manage.py create_platform_admin` or by another operator. */
import { useEffect, useState } from 'react'
import { useAuth } from './auth'
import { Icon } from './shared/Icon'
import { Button, ErrorBox, Field } from './shared/ui'
import { api } from './shared/api'

export default function Login() {
  const { login, verifyOtp, resendOtp, testLogin } = useAuth()
  const [step, setStep] = useState<'signin' | 'otp'>('signin')
  const [identifier, setIdentifier] = useState('')
  const [password, setPassword] = useState('')
  const [shown, setShown] = useState(false)
  const [code, setCode] = useState('')
  const [sentTo, setSentTo] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [cooldown, setCooldown] = useState(0)

  useEffect(() => {
    if (cooldown <= 0) return
    const t = setTimeout(() => setCooldown(cooldown - 1), 1000)
    return () => clearTimeout(t)
  }, [cooldown])

  const run = async (fn: () => Promise<void>) => {
    setBusy(true)
    setError(null)
    try {
      await fn()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }
  const verify = (c: string) => run(() => verifyOtp(c))
  const takeCode = (raw: string) => {
    const v = raw.replace(/\D/g, '').slice(0, 6)
    setCode(v)
    if (v.length === 6 && !busy) void verify(v)
  }

  return (
    <div className="console-login">
      <div className="console-login-brand">
        <span className="brand-mark" aria-hidden><Icon name="receipt" size={20} /></span>
        <span>IMS Console <span className="muted">· operators</span></span>
      </div>
      <div className="console-login-card">
        {step === 'signin' ? (
          <form onSubmit={(e) => {
            e.preventDefault()
            void run(async () => {
              const r = await login(identifier.trim(), password)
              if (r.otp) {
                setSentTo(r.sentTo)
                setCooldown(60)
                setStep('otp')
              }
            })
          }}>
            <h2>Operator sign-in</h2>
            <p className="muted">Not for shop staff: the IMS app has its own sign-in</p>
            <ErrorBox error={error} />
            <Field label="Username or email">
              <input autoFocus autoComplete="username" autoCapitalize="none" spellCheck={false} value={identifier}
                onChange={(e) => setIdentifier(e.target.value)} required />
            </Field>
            <Field label="Password">
              <span className="password-input">
                <input type={shown ? 'text' : 'password'} autoComplete="current-password" value={password}
                  onChange={(e) => setPassword(e.target.value)} required />
                <button type="button" className="link" aria-pressed={shown} onClick={() => setShown(!shown)}>{shown ? 'Hide' : 'Show'}</button>
              </span>
            </Field>
            <Button kind="primary" type="submit" busy={busy} className="btn-block btn-xl">Continue</Button>
            <p className="muted small console-login-note">Forgot your password? Another operator can send you a reset code, or run <code>manage.py changepassword</code> on the server</p>
          </form>
        ) : (
          <form onSubmit={(e) => { e.preventDefault(); void verify(code) }}>
            <h2>Enter your sign-in code</h2>
            <p className="muted">Sent to <strong>{sentTo}</strong>, valid for 10 minutes</p>
            {notice && <div className="alert alert-good">{notice}</div>}
            <ErrorBox error={error} />
            <Field label="6-digit code">
              <span className="password-input">
                <input className="code-input" autoFocus inputMode="numeric" autoComplete="one-time-code" maxLength={6}
                  value={code} onChange={(e) => takeCode(e.target.value)} required />
                {!!navigator.clipboard?.readText && (
                  <button type="button" className="link" onClick={async () => {
                    try {
                      takeCode(await navigator.clipboard.readText())
                    } catch {
                      setError('Your browser blocked pasting. Press Ctrl+V in the box instead')
                    }
                  }}>Paste</button>
                )}
              </span>
            </Field>
            <Button kind="primary" type="submit" busy={busy} className="btn-block btn-xl">Sign in</Button>
            <p className="login-links">
              <button type="button" className="link" disabled={cooldown > 0 || busy} onClick={() => void run(async () => {
                setSentTo(await resendOtp())
                setCooldown(60)
                setNotice('A new code is on its way')
              })}>{cooldown > 0 ? `Resend code in ${cooldown}s` : 'Resend code'}</button>
              <button type="button" className="link" onClick={() => { setStep('signin'); setCode(''); setError(null) }}>Back</button>
            </p>
          </form>
        )}
      </div>
      <p className="console-login-foot">Restricted to platform operators · every action is recorded in the audit log</p>
      <TestLogin onPick={(u) => run(() => testLogin(u))} />
    </div>
  )
}

/** Testing phase only (server IMS_TEST_LOGIN): one-click sign-in. Renders
 *  nothing unless the server allows it for this network. */
function TestLogin({ onPick }: { onPick: (username: string) => void }) {
  const [users, setUsers] = useState<{ username: string; name: string; roles: string[] }[] | null>(null)
  useEffect(() => {
    api<{ users: { username: string; name: string; roles: string[] }[] }>('auth/test-login').then((r) => setUsers(r.users)).catch(() => setUsers(null))
  }, [])
  if (!users) return null
  // Operators hold no business roles; list them first.
  const sorted = [...users].sort((a, b) => a.roles.length - b.roles.length)
  return (
    <section className="console-login-card test-login" aria-label="Test sign-in">
      <h2>Test sign-in</h2>
      <p className="muted small">Testing phase only (IMS_TEST_LOGIN). Only operator accounts can use the console</p>
      <ul>
        {sorted.map((u) => (
          <li key={u.username}>
            <button className="test-login-user" onClick={() => onPick(u.username)}>
              <span><strong>{u.name}</strong> <span className="mono muted">@{u.username}</span></span>
              <span className="muted small">{u.roles.join(', ') || 'operator / no business role'}</span>
            </button>
          </li>
        ))}
      </ul>
    </section>
  )
}
