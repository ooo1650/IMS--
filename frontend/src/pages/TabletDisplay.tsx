/**
 * /display: a tablet (or any other device with a browser) as the customer
 * display. No login: a one-time code from POS → Devices gives a token that can
 * read that one till's customer screen (backend/sales/display.py).
 */
import { useCallback, useEffect, useState } from 'react'
import { CustomerScreen } from '../components/CustomerScreen'
import { Button, ErrorBox, Field } from '../components/ui'
import { ApiError, api } from '../lib/api'
import type { DisplayState } from '../lib/devices/display'

const TOKEN_KEY = 'ims.display'
/** After losing the server, how long a bill may stay up. A wrong total is worse than none. */
const BLANK_AFTER_MS = 15_000
/** With nothing sold for this long, stop asking the server (tap to start again). */
const SLEEP_AFTER_S = 3 * 3600

/** Quick during a sale, slower when quiet: the hosting counts every request. */
function pollDelay(quietSeconds: number) {
  return quietSeconds < 60 ? 1500 : quietSeconds < 900 ? 3000 : 5000
}

function savedToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY)
  } catch {
    return null
  }
}

function saveToken(token: string | null) {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token)
    else localStorage.removeItem(TOKEN_KEY)
  } catch {
    /* storage blocked: connected until the page is closed */
  }
}

/** Keep the screen lit while it's showing (needs https; not on very old browsers). */
function useKeepAwake(on: boolean) {
  useEffect(() => {
    if (!on || !('wakeLock' in navigator)) return
    let lock: WakeLockSentinel | null = null
    let ended = false
    const get = () => {
      if (document.visibilityState !== 'visible') return
      navigator.wakeLock.request('screen').then((l) => {
        if (ended) return void l.release()
        lock = l
      }).catch(() => undefined) // battery saver can refuse
    }
    get()
    document.addEventListener('visibilitychange', get)
    return () => {
      ended = true
      document.removeEventListener('visibilitychange', get)
      void lock?.release()
    }
  }, [on])
}

function useFullscreen() {
  const [full, setFull] = useState(!!document.fullscreenElement)
  useEffect(() => {
    const changed = () => setFull(!!document.fullscreenElement)
    document.addEventListener('fullscreenchange', changed)
    return () => document.removeEventListener('fullscreenchange', changed)
  }, [])
  const enter = () => void document.documentElement.requestFullscreen?.().catch(() => undefined)
  return { canEnter: !full && !!document.fullscreenEnabled, enter }
}

interface Reply { version: string; quiet_seconds: number; state?: DisplayState; till?: string }

export default function TabletDisplay() {
  const [token, setToken] = useState(savedToken)
  const [screen, setScreen] = useState<DisplayState | null>(null)
  const [till, setTill] = useState('')
  const [lost, setLost] = useState<string | null>(null)
  const [asleep, setAsleep] = useState(false)
  const [code, setCode] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const { canEnter, enter } = useFullscreen()
  useKeepAwake(!!token && !asleep)

  const claim = useCallback(async (raw: string) => {
    setError(null)
    setBusy(true)
    try {
      const r = await api<{ token: string; till: string }>('display/claim', { body: { code: raw } })
      saveToken(r.token)
      setScreen(null)
      setAsleep(false)
      setTill(r.till)
      setToken(r.token)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }, [])

  // The QR code opens /display#CODE. The part after # never reaches a server log; drop it from the history too.
  useEffect(() => {
    document.title = 'Customer display'
    const fromLink = () => {
      const linked = window.location.hash.slice(1)
      if (!linked) return
      window.history.replaceState(null, '', window.location.pathname)
      void claim(linked)
    }
    fromLink()
    window.addEventListener('hashchange', fromLink)
    return () => window.removeEventListener('hashchange', fromLink)
  }, [claim])

  useEffect(() => {
    if (!token || asleep) return
    let stopped = false
    let busyPolling = false
    let timer: ReturnType<typeof setTimeout> | undefined
    let version = ''
    let failures = 0
    let mode: DisplayState['mode'] = 'idle'
    const startedAt = Date.now()
    let lastOk = startedAt

    const poll = async () => {
      clearTimeout(timer)
      if (stopped || busyPolling || document.hidden) return
      busyPolling = true
      let delay: number
      try {
        const query = version ? `?version=${encodeURIComponent(version)}` : ''
        const r = await api<Reply>(`display/state${query}`, { headers: { 'X-Display-Token': token } })
        if (stopped) return
        failures = 0
        lastOk = Date.now()
        setLost(null)
        version = r.version
        if (r.state) {
          mode = r.state.mode
          setScreen(r.state)
          if (r.till) setTill(r.till)
        }
        // Counted from waking up, so a tap gives a fresh three hours.
        const quiet = Math.min(r.quiet_seconds, (Date.now() - startedAt) / 1000)
        if (mode === 'idle' && quiet > SLEEP_AFTER_S) return setAsleep(true)
        delay = pollDelay(r.quiet_seconds)
      } catch (e) {
        if (stopped) return
        if (e instanceof ApiError && e.code === 'display_unpaired') {
          saveToken(null)
          setToken(null)
          setScreen(null)
          setLost(null)
          setError(e.message)
          return
        }
        failures++
        delay = Math.min(10_000, 1500 * failures)
        if (Date.now() - lastOk > BLANK_AFTER_MS) {
          setScreen(null)
          version = '' // fetch the screen in full once the server is back
          setLost((e as Error).message)
        }
      } finally {
        busyPolling = false
      }
      timer = setTimeout(poll, delay)
    }

    const visible = () => void poll()
    void poll()
    document.addEventListener('visibilitychange', visible)
    return () => {
      stopped = true
      clearTimeout(timer)
      document.removeEventListener('visibilitychange', visible)
    }
  }, [token, asleep])

  if (!token) {
    return (
      <div className="cd cd-idle">
        <form className="cd-pair" onSubmit={(e) => { e.preventDefault(); void claim(code) }}>
          <h1>Customer display</h1>
          <p>On the till open <strong>POS → Devices → Connect a tablet</strong>. Scan the QR code it shows with this tablet, or type the code here.</p>
          <ErrorBox error={error} />
          <Field label="Code from the till">
            <input className="cd-code" autoCapitalize="characters" autoComplete="off" autoCorrect="off" spellCheck={false}
              placeholder="ABCD-EFGH" maxLength={12} value={code} onChange={(e) => setCode(e.target.value)} />
          </Field>
          <Button kind="primary" type="submit" className="btn-xl btn-block" busy={busy}
            disabled={code.replace(/[^a-z0-9]/gi, '').length !== 8}>Connect</Button>
        </form>
      </div>
    )
  }

  if (asleep) {
    return (
      <button type="button" className="cd cd-idle cd-sleep" onClick={() => setAsleep(false)}>
        <strong>Tap to start</strong>
        <span>Customer display · {till}</span>
      </button>
    )
  }

  return (
    <CustomerScreen s={screen} footer={(
      <>
        {error && <p className="cd-hint" role="alert">{error}</p>}
        {lost && <p className="cd-hint" role="status">Reconnecting to the till… {lost}</p>}
        {till && <p className="cd-hint">{till}</p>}
        {canEnter && <Button onClick={enter}>Full screen</Button>}
      </>
    )} />
  )
}
