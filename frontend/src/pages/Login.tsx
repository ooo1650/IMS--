import { useEffect, useState } from 'react'
import { useAuth } from '../auth'
import { Icon } from '../components/Icon'
import { Button, ErrorBox, Field } from '../components/ui'
import { api } from '../lib/api'
import { useOnline } from '../lib/sync'

type Step = 'signin' | 'otp' | 'forgot' | 'reset' | 'signup'
interface Brand { name: string; name_np: string; today_ad: string; today_bs: string; today_bs_text: string }

const adDate = (iso: string) =>
  new Date(`${iso}T00:00:00`).toLocaleDateString('en-GB', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' })

/** Password field with a Show/Hide toggle and a Caps Lock warning (shared tills). */
function PasswordInput({ value, onChange, autoComplete, autoFocus, minLength }: {
  value: string; onChange: (v: string) => void; autoComplete: string; autoFocus?: boolean; minLength?: number
}) {
  const [shown, setShown] = useState(false)
  const [caps, setCaps] = useState(false)
  const track = (e: React.KeyboardEvent<HTMLInputElement>) => setCaps(e.getModifierState('CapsLock'))
  return (
    <>
      <span className="password-input">
        <input type={shown ? 'text' : 'password'} autoComplete={autoComplete} autoFocus={autoFocus} minLength={minLength}
          value={value} onChange={(e) => onChange(e.target.value)} onKeyUp={track} onKeyDown={track} onBlur={() => setCaps(false)} required />
        <button type="button" className="link" aria-pressed={shown} onClick={() => setShown(!shown)}>{shown ? 'Hide' : 'Show'}</button>
      </span>
      {caps && <span className="field-hint caps-hint" role="status">Caps Lock is on</span>}
    </>
  )
}

export default function Login() {
  const { login, verifyOtp, resendOtp, signUp } = useAuth()
  const online = useOnline()
  // /signup opens "Create your account" (for people who got an invite code).
  const [step, setStep] = useState<Step>(window.location.pathname === '/signup' ? 'signup' : 'signin')
  const [names, setNames] = useState({ first_name: '', last_name: '' })
  const [identifier, setIdentifier] = useState('')
  const [password, setPassword] = useState('')
  const [code, setCode] = useState('')
  const [sentTo, setSentTo] = useState('')
  const [email, setEmail] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [cooldown, setCooldown] = useState(0)
  const [brand, setBrand] = useState<Brand | null>(null)

  useEffect(() => {
    // Retry a few times: right after a server restart the first request can fail.
    let cancelled = false
    const load = (attempt: number) =>
      api<Brand>('auth/brand').then((b) => { if (!cancelled) setBrand(b) })
        .catch(() => { if (!cancelled && attempt < 4) setTimeout(() => load(attempt + 1), 2000 * attempt) })
    load(1)
    return () => { cancelled = true }
  }, [])

  useEffect(() => {
    if (cooldown <= 0) return
    const t = setTimeout(() => setCooldown(cooldown - 1), 1000)
    return () => clearTimeout(t)
  }, [cooldown])

  const go = (next: Step, message: string | null = null) => {
    setStep(next)
    setError(null)
    setNotice(message)
    setCode('')
  }

  /** Runs a step's action with the busy flag and error display. */
  const run = (fn: () => Promise<void>) => async (e?: React.FormEvent) => {
    e?.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await fn()
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const signIn = run(async () => {
    const r = await login(identifier.trim(), password)
    if (r.otp) {
      setSentTo(r.sentTo)
      setCooldown(60)
      go('otp')
    }
  })
  const verify = run(() => verifyOtp(code))
  // Auto-submit gets the 6 digits directly: `code` state is still the previous keystroke here.
  const verifyCode = (c: string) => void run(() => verifyOtp(c))()
  const resend = run(async () => {
    setSentTo(await resendOtp())
    setCooldown(60)
    setNotice('A new code is on its way')
  })
  const requestReset = run(async () => {
    await api('auth/csrf')
    const r = await api<{ detail: string }>('auth/password/forgot', { body: { email: email.trim() } })
    setCooldown(60)
    go('reset', r.detail)
  })
  /** Same endpoint and reply as "Forgot password?": a code goes out only if an
   *  active account uses that email, and the reply never says which. */
  const sendSignupCode = run(async () => {
    await api('auth/csrf')
    await api('auth/password/forgot', { body: { email: email.trim() } })
    setCooldown(60)
    setNotice(`If ${email.trim()} was added by your administrator, a code is on its way. It expires in 15 minutes`)
  })
  const signup = run(async () => {
    if (newPassword !== confirm) throw new Error('The two passwords don\'t match')
    await signUp({ email: email.trim(), code, password: newPassword, ...names })
    window.history.replaceState(null, '', '/')
  })
  const reset = run(async () => {
    if (newPassword !== confirm) throw new Error('The two passwords don\'t match')
    const r = await api<{ detail: string }>('auth/password/reset', { body: { email: email.trim(), code, password: newPassword } })
    setPassword('')
    setNewPassword('')
    setConfirm('')
    setIdentifier(email.trim())
    go('signin', r.detail)
  })

  /** Digits only (pasting "123 456" works); the sign-in step submits itself at 6 digits. */
  const takeCode = (raw: string, autoSubmit?: (code: string) => void) => {
    const v = raw.replace(/\D/g, '').slice(0, 6)
    setCode(v)
    if (v.length === 6 && autoSubmit && !busy) autoSubmit(v)
  }
  const canPaste = typeof navigator !== 'undefined' && !!navigator.clipboard?.readText
  const codeInput = (autoSubmit?: (code: string) => void) => (
    <Field label="6-digit code">
      <span className="password-input">
        <input className="code-input" autoFocus inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]{6}" maxLength={6}
          aria-describedby="code-help" value={code} required onChange={(e) => takeCode(e.target.value, autoSubmit)} />
        {canPaste && (
          <button type="button" className="link" title="Paste the code you copied from the email"
            onClick={async () => {
              try {
                takeCode(await navigator.clipboard.readText(), autoSubmit)
              } catch {
                setError('Your browser blocked pasting. Press Ctrl+V (or long-press) in the box instead')
              }
            }}>Paste</button>
        )}
      </span>
    </Field>
  )

  return (
    <div className="login-wrap">
      {/* The page's one moment: a day-open slip from the till printer, on the
          counter. Only true facts: whose system, today in BS and AD, Nepal time
          and the connection. Same paper, torn edge and mono figures as the POS bill. */}
      <header className="login-id">
        <div className="login-brand">
          <span className="brand-mark" aria-hidden><Icon name="receipt" size={20} /></span>
          <span>IMS <span className="muted">· Inventory &amp; POS</span></span>
        </div>
        <div className="day-slip-wrap">
          <article className="day-slip" aria-label="Today">
            <header className="day-slip-head">
              <strong>{brand?.name || 'IMS'}</strong>
              {brand?.name_np && <span lang="ne">{brand.name_np}</span>}
              <span>Inventory &amp; POS</span>
            </header>
            <p className="day-slip-title">Day open</p>
            {brand ? (
              <dl className="day-slip-rows">
                <div><dt>Date (BS)</dt><dd>{brand.today_bs}</dd></div>
                <div className="day-slip-sub"><dt /><dd>{brand.today_bs_text}</dd></div>
                <div><dt>Date (AD)</dt><dd>{brand.today_ad}</dd></div>
                <div className="day-slip-sub"><dt /><dd>{adDate(brand.today_ad).split(',')[0]}</dd></div>
                <div><dt>Time (NPT)</dt><dd><NepalClock /></dd></div>
              </dl>
            ) : <p className="day-slip-sub">…</p>}
            <dl className="day-slip-rows day-slip-status">
              <div><dt>Server</dt><dd><span className={`status-dot ${online ? '' : 'off'}`} aria-hidden /> {online ? 'online' : 'offline'}</dd></div>
            </dl>
            {!online && <p className="day-slip-note">Offline: only cashiers who signed in on this device in the last 24 hours can sign in</p>}
          </article>
        </div>
      </header>
      <main className="login-main">
      <div className="login-side">
        {step === 'signin' && (
          <form className="login-card" onSubmit={signIn}>
            <h2>Sign in</h2>
            {notice && <div className="alert alert-good">{notice}</div>}
            <ErrorBox error={error} />
            <Field label="Username or email">
              <input autoFocus autoComplete="username" autoCapitalize="none" spellCheck={false} value={identifier} onChange={(e) => setIdentifier(e.target.value)} required />
            </Field>
            <Field label="Password">
              <PasswordInput autoComplete="current-password" value={password} onChange={setPassword} />
            </Field>
            <Button kind="primary" type="submit" busy={busy} className="btn-block btn-xl">
              Sign in
            </Button>
            <p className="login-links">
              <button type="button" className="link" disabled={!online} title={online ? '' : 'Password resets need a connection'}
                onClick={() => { setEmail(identifier.includes('@') ? identifier.trim() : ''); go('forgot') }}>
                Forgot password?
              </button>
              <button type="button" className="link" onClick={() => { setEmail(identifier.includes('@') ? identifier.trim() : ''); go('signup') }}>
                Got an invite? Create your account
              </button>
            </p>
          </form>
        )}

        {step === 'otp' && (
          <form className="login-card" onSubmit={verify}>
            <h2>Enter your sign-in code</h2>
            <p className="muted" id="code-help">Sent to <strong>{sentTo}</strong>, valid for 10 minutes</p>
            {notice && <div className="alert alert-good">{notice}</div>}
            <ErrorBox error={error} />
            {codeInput(verifyCode)}
            <Button kind="primary" type="submit" busy={busy} className="btn-block btn-xl">Verify and sign in</Button>
            <p className="login-links">
              <button type="button" className="link" disabled={cooldown > 0 || busy} onClick={() => void resend()}>
                {cooldown > 0 ? `Resend code in ${cooldown}s` : 'Resend code'}
              </button>
              <button type="button" className="link" onClick={() => { setPassword(''); go('signin') }}>Use a different account</button>
            </p>
          </form>
        )}

        {step === 'signup' && (
          <form className="login-card" onSubmit={signup}>
            <h2>Create your account</h2>
            <p className="muted" id="code-help">For people your administrator has added: enter the email they used, get a code, then choose your password</p>
            {notice && <div className="alert alert-good">{notice}</div>}
            <ErrorBox error={error} />
            <Field label="Email">
              <span className="password-input">
                <input autoFocus type="email" autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} required
                  style={{ paddingRight: 136 }} />
                <button type="button" className="link" disabled={!email.includes('@') || cooldown > 0 || busy || !online}
                  title={online ? '' : 'Needs a connection'} onClick={() => void sendSignupCode()}>
                  {cooldown > 0 ? `Resend in ${cooldown}s` : 'Send me a code'}
                </button>
              </span>
            </Field>
            {codeInput()}
            <div className="form-grid">
              <Field label="First name"><input autoComplete="given-name" value={names.first_name} onChange={(e) => setNames({ ...names, first_name: e.target.value })} /></Field>
              <Field label="Last name"><input autoComplete="family-name" value={names.last_name} onChange={(e) => setNames({ ...names, last_name: e.target.value })} /></Field>
            </div>
            <Field label="Choose a password" hint="At least 10 characters; not a common password">
              <PasswordInput autoComplete="new-password" minLength={10} value={newPassword} onChange={setNewPassword} />
            </Field>
            <Field label="Confirm password">
              <input type="password" autoComplete="new-password" minLength={10} value={confirm} onChange={(e) => setConfirm(e.target.value)} required />
            </Field>
            <Button kind="primary" type="submit" busy={busy} className="btn-block btn-xl">Create account and sign in</Button>
            <p className="login-links">
              <button type="button" className="link" onClick={() => { window.history.replaceState(null, '', '/'); go('signin') }}>I already have an account</button>
            </p>
          </form>
        )}

        {step === 'forgot' && (
          <form className="login-card" onSubmit={requestReset}>
            <h2>Reset your password</h2>
            <p className="muted">We'll email a 6-digit code to the address on your account</p>
            <ErrorBox error={error} />
            <Field label="Email">
              <input autoFocus type="email" autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
            </Field>
            <Button kind="primary" type="submit" busy={busy} className="btn-block btn-xl">Send reset code</Button>
            <p className="login-links">
              <button type="button" className="link" onClick={() => go('reset')}>I already have a code</button>
              <button type="button" className="link" onClick={() => go('signin')}>Back to sign in</button>
            </p>
          </form>
        )}

        {step === 'reset' && (
          <form className="login-card" onSubmit={reset}>
            <h2>Choose a new password</h2>
            <p className="muted" id="code-help">Enter the code emailed to {email || 'you'} and a new password</p>
            {notice && <div className="alert alert-good">{notice}</div>}
            <ErrorBox error={error} />
            <Field label="Email">
              <input type="email" autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
            </Field>
            {codeInput()}
            <Field label="New password" hint="At least 10 characters; not a common password">
              <PasswordInput autoComplete="new-password" minLength={10} value={newPassword} onChange={setNewPassword} />
            </Field>
            <Field label="Confirm new password">
              <input type="password" autoComplete="new-password" minLength={10} value={confirm} onChange={(e) => setConfirm(e.target.value)} required />
            </Field>
            <Button kind="primary" type="submit" busy={busy} className="btn-block btn-xl">Change password</Button>
            <p className="login-links">
              <button type="button" className="link" disabled={cooldown > 0 || busy} onClick={() => void requestReset()}>
                {cooldown > 0 ? `Send a new code in ${cooldown}s` : 'Send a new code'}
              </button>
              <button type="button" className="link" onClick={() => go('signin')}>Back to sign in</button>
            </p>
          </form>
        )}
      </div>
      <TestLoginPanel />
      <p className="login-foot">Accounts and branch access are set up by your administrator</p>
      </main>
    </div>
  )
}

/** TESTING PHASE ONLY: one-click sign-in as any user. The server answers 404
 *  unless IMS_TEST_LOGIN is on and the request is from a private network, so
 *  on a live server this renders nothing. */
function TestLoginPanel() {
  const { testLogin } = useAuth()
  const [users, setUsers] = useState<{ username: string; name: string; roles: string[] }[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState('')
  useEffect(() => {
    api<{ users: { username: string; name: string; roles: string[] }[] }>('auth/test-login')
      .then((r) => setUsers(r.users))
      .catch(() => setUsers(null))
  }, [])
  if (!users) return null
  return (
    <section className="login-side test-login" aria-label="Test sign-in">
      <h2>Test sign-in</h2>
      <p className="muted small">Testing phase only: no password or emailed code. Turn off with IMS_TEST_LOGIN=false</p>
      <ErrorBox error={error} />
      <ul>
        {users.map((u) => (
          <li key={u.username}>
            <button className="test-login-user" disabled={!!busy} onClick={async () => {
              setBusy(u.username)
              setError(null)
              try {
                await testLogin(u.username)
              } catch (e) {
                setError((e as Error).message)
              } finally {
                setBusy('')
              }
            }}>
              <span><strong>{u.name}</strong> <span className="mono muted">@{u.username}</span></span>
              <span className="muted small">{u.roles.join(', ') || 'no roles'}</span>
            </button>
          </li>
        ))}
      </ul>
    </section>
  )
}

/** Nepal time, ticking each minute (the slip's one live figure). */
function NepalClock() {
  const fmt = () => new Intl.DateTimeFormat('en-GB', { hour: '2-digit', minute: '2-digit', timeZone: 'Asia/Kathmandu' }).format(new Date())
  const [now, setNow] = useState(fmt)
  useEffect(() => {
    const t = setInterval(() => setNow(fmt()), 15000)
    return () => clearInterval(t)
  }, [])
  return <>{now}</>
}
