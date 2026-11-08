/** The signed-in user's own security settings: password, email, sign-in code, sessions. */
import { useState } from 'react'
import { useAuth } from '../auth'
import { Badge, Button, ErrorBox, Field, PageHead } from '../components/ui'
import { api } from '../lib/api'
import type { Me } from '../lib/types'

/** One card's busy / error / success state around an API call. */
function useAction() {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState<string | null>(null)
  const run = async (fn: () => Promise<string | void>) => {
    setBusy(true)
    setError(null)
    setDone(null)
    try {
      const msg = await fn()
      if (msg) setDone(msg)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }
  return { busy, error, done, run }
}

export default function Account() {
  const { me, offlineSession } = useAuth()
  if (!me) return null
  return (
    <div>
      <PageHead title="My account" sub="Your sign-in details and security" />
      {offlineSession && <div className="alert alert-warn">You're signed in offline. Account changes need a connection.</div>}
      <div className="card narrow">
        <div className="card-head"><h2>{me.name}</h2></div>
        <dl className="dl">
          <dt>Username</dt><dd className="mono">{me.username}</dd>
          <dt>Email</dt>
          <dd>{me.email || <span className="muted">none</span>} {me.email && (me.email_verified ? <Badge tone="good">verified</Badge> : <Badge tone="warn">not verified</Badge>)}</dd>
          <dt>Sign-in code</dt><dd>{me.otp_required ? <Badge tone="good">on</Badge> : <Badge>off</Badge>}</dd>
        </dl>
      </div>
      {!offlineSession && (
        <>
          <ChangePassword />
          <ChangeEmail />
          <SignInCode me={me} />
          <Sessions />
        </>
      )}
    </div>
  )
}

function ChangePassword() {
  const a = useAction()
  const [f, setF] = useState({ current: '', next: '', confirm: '' })
  return (
    <form className="card narrow" onSubmit={(e) => {
      e.preventDefault()
      void a.run(async () => {
        if (f.next !== f.confirm) throw new Error('The two new passwords don\'t match.')
        await api('auth/password/change', { body: { current_password: f.current, new_password: f.next } })
        setF({ current: '', next: '', confirm: '' })
        return 'Password changed. Your other devices have been signed out.'
      })
    }}>
      <h2>Change password</h2>
      <ErrorBox error={a.error} />
      {a.done && <div className="alert alert-good">{a.done}</div>}
      <Field label="Current password"><input type="password" autoComplete="current-password" value={f.current} onChange={(e) => setF({ ...f, current: e.target.value })} required /></Field>
      <Field label="New password" hint="At least 10 characters; not a common password"><input type="password" autoComplete="new-password" minLength={10} value={f.next} onChange={(e) => setF({ ...f, next: e.target.value })} required /></Field>
      <Field label="Confirm new password"><input type="password" autoComplete="new-password" minLength={10} value={f.confirm} onChange={(e) => setF({ ...f, confirm: e.target.value })} required /></Field>
      <Button kind="primary" type="submit" busy={a.busy}>Change password</Button>
    </form>
  )
}

function ChangeEmail() {
  const { refresh } = useAuth()
  const a = useAction()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [sentTo, setSentTo] = useState<string | null>(null)
  const [code, setCode] = useState('')
  return (
    <form className="card narrow" onSubmit={(e) => {
      e.preventDefault()
      void a.run(async () => {
        if (!sentTo) {
          setSentTo((await api<{ sent_to: string }>('auth/email', { body: { email, password } })).sent_to)
          setPassword('')
          return
        }
        await api<Me>('auth/email/verify', { body: { code } })
        await refresh()
        setSentTo(null)
        setCode('')
        setEmail('')
        return 'Email changed and verified.'
      })
    }}>
      <h2>Change email</h2>
      <p className="card-sub">Sign-in and password reset codes go to this address</p>
      <ErrorBox error={a.error} />
      {a.done && <div className="alert alert-good">{a.done}</div>}
      {!sentTo ? (
        <>
          <Field label="New email"><input type="email" autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} required /></Field>
          <Field label="Your password"><input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required /></Field>
          <Button kind="primary" type="submit" busy={a.busy}>Send confirmation code</Button>
        </>
      ) : (
        <>
          <p>Enter the code we sent to <strong>{sentTo}</strong>.</p>
          <Field label="6-digit code"><input autoFocus inputMode="numeric" autoComplete="one-time-code" maxLength={7} value={code} onChange={(e) => setCode(e.target.value)} required /></Field>
          <div className="flags">
            <Button kind="primary" type="submit" busy={a.busy}>Confirm email</Button>
            <Button kind="ghost" onClick={() => setSentTo(null)}>Cancel</Button>
          </div>
        </>
      )}
    </form>
  )
}

function SignInCode({ me }: { me: Me }) {
  const { refresh } = useAuth()
  const a = useAction()
  const [password, setPassword] = useState('')
  const forced = me.otp_required && !me.otp_enabled
  return (
    <form className="card narrow" onSubmit={(e) => {
      e.preventDefault()
      void a.run(async () => {
        await api('auth/2fa', { body: { enabled: !me.otp_enabled, password } })
        await refresh()
        setPassword('')
        return me.otp_enabled ? 'Sign-in code turned off.' : 'Sign-in code turned on. You\'ll need it next time you sign in.'
      })
    }}>
      <h2>Sign-in code (two-step sign-in)</h2>
      <p className="card-sub">After your password, enter a 6-digit code we email you. Someone who learns your password still can't sign in.</p>
      <ErrorBox error={a.error} />
      {a.done && <div className="alert alert-good">{a.done}</div>}
      {forced ? (
        <p className="muted">Your role requires a sign-in code, so it's always on</p>
      ) : !me.email ? (
        <p className="muted">Add an email address first</p>
      ) : (
        <>
          <Field label="Your password"><input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required /></Field>
          <Button kind={me.otp_enabled ? 'ghost' : 'primary'} type="submit" busy={a.busy}>{me.otp_enabled ? 'Turn off' : 'Turn on'}</Button>
        </>
      )}
    </form>
  )
}

function Sessions() {
  const a = useAction()
  return (
    <div className="card narrow">
      <h2>Signed-in devices</h2>
      <p className="card-sub">Lost a phone or used a shared computer? Sign out everywhere except here.</p>
      <ErrorBox error={a.error} />
      {a.done && <div className="alert alert-good">{a.done}</div>}
      <Button busy={a.busy} onClick={() => void a.run(async () => {
        await api('auth/sign-out-everywhere', { method: 'POST' })
        return 'All your other sessions have been signed out.'
      })}>Sign out other devices</Button>
    </div>
  )
}
