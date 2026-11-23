/** Operator sign-in: password, then the emailed code (always required for
 *  operators). Forgot password: a code to the account's email (same endpoints
 *  as the shop app), and the emailed sign-in code is still asked for afterwards.
 *  No sign-up and no offline mode: operator accounts are created with
 *  `manage.py create_platform_admin` or by another operator. */
import { useEffect, useState } from 'react'
import { useAuth } from './auth'
import { Icon } from './shared/Icon'
import { Button, ErrorBox, Field } from './shared/ui'
import { ApiError, api, formatWait } from './shared/api'

export default function Login() {
  const { login, verifyOtp, resendOtp, testLogin } = useAuth()
  const [step, setStep] = useState<'signin' | 'otp' | 'forgot' | 'reset'>('signin')
  const [email, setEmail] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirm, setConfirm] = useState('')
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
      if (e instanceof ApiError) {
        if (e.code === 'login_expired') { setStep('signin'); setCode('') }
        if (e.code === 'otp_wait' && e.retryAfter) setCooldown(e.retryAfter)
        if (e.code === 'otp_spent') { setCode(''); setCooldown(0) }
        if (e.code === 'csrf') await api('auth/csrf').catch(() => {})
      }
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }
  const go = (next: typeof step, message: string | null = null) => {
    setStep(next)
    setError(null)
    setNotice(message)
    setCode('')
  }
  const requestReset = () => run(async () => {
    await api('auth/csrf')
    const r = await api<{ detail: string }>('auth/password/forgot', { body: { email: email.trim() } })
    setCooldown(60)
    go('reset', r.detail)
  })
  const reset = () => run(async () => {
    if (newPassword !== confirm) throw new Error("The two passwords don't match")
    const r = await api<{ detail: string }>('auth/password/reset', { body: { email: email.trim(), code, password: newPassword } })
    setPassword('')
    setNewPassword('')
    setConfirm('')
    setIdentifier(email.trim())
    go('signin', r.detail)
  })
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
            {notice && <div className="alert alert-good">{notice}</div>}
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
            <p className="login-links">
              <button type="button" className="link" onClick={() => { setEmail(identifier.includes('@') ? identifier.trim() : ''); go('forgot') }}>
                Forgot password?
              </button>
            </p>
          </form>
        ) : step === 'forgot' ? (
          <form onSubmit={(e) => { e.preventDefault(); void requestReset() }}>
            <h2>Reset your password</h2>
            <p className="muted">We'll email a 6-digit code to the address on your operator account</p>
            <ErrorBox error={error} />
            <Field label="Email">
              <input autoFocus type="email" autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
            </Field>
            <Button kind="primary" type="submit" busy={busy} className="btn-block btn-xl">Send reset code</Button>
            <p className="login-links">
              <button type="button" className="link" onClick={() => go('reset')}>I already have a code</button>
              <button type="button" className="link" onClick={() => go('signin')}>Back</button>
            </p>
            <p className="muted small console-login-note">No email arriving? On the server run <code>manage.py create_platform_admin --email you@example.com --reset</code> (hosted: <code>scripts/hosted_operator.py</code>)</p>
          </form>
        ) : step === 'reset' ? (
          <form onSubmit={(e) => { e.preventDefault(); void reset() }}>
            <h2>Choose a new password</h2>
            <p className="muted">Enter the code emailed to {email || 'you'} and a new password</p>
            {notice && <div className="alert alert-good">{notice}</div>}
            <ErrorBox error={error} />
            <Field label="Email">
              <input type="email" autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
            </Field>
            <Field label="6-digit code">
              <input className="code-input" inputMode="numeric" autoComplete="one-time-code" maxLength={6} value={code}
                onChange={(e) => setCode(e.target.value.replace(/\D/g, '').slice(0, 6))} required />
            </Field>
            <Field label="New password" hint="At least 10 characters; not a common password">
              <input type="password" autoComplete="new-password" minLength={10} value={newPassword} onChange={(e) => setNewPassword(e.target.value)} required />
            </Field>
            <Field label="Confirm new password">
              <input type="password" autoComplete="new-password" minLength={10} value={confirm} onChange={(e) => setConfirm(e.target.value)} required />
            </Field>
            <Button kind="primary" type="submit" busy={busy} className="btn-block btn-xl">Change password</Button>
            <p className="login-links">
              <button type="button" className="link" disabled={cooldown > 0 || busy} onClick={() => void requestReset()}>
                {cooldown > 0 ? `Send a new code in ${formatWait(cooldown)}` : 'Send a new code'}
              </button>
              <button type="button" className="link" onClick={() => go('signin')}>Back</button>
            </p>
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
              })}>{cooldown > 0 ? `Resend code in ${formatWait(cooldown)}` : 'Resend code'}</button>
              <button type="button" className="link" onClick={() => go('signin')}>Back</button>
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
