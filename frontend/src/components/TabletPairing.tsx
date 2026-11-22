/** POS → Devices: connect a tablet as this till's customer display. */
import { useCallback, useEffect, useState } from 'react'
import { api } from '../lib/api'
import { useDeviceSettings } from '../lib/devices/settings'
import type { RegisterCfg } from '../lib/types'
import { Badge, Button, ErrorBox } from './ui'

interface Status { paired: boolean; online: boolean; code_pending: boolean }
interface Pairing { code: string; link: string; until: number; qr?: string }

export function TabletPairing({ register }: { register: RegisterCfg }) {
  const [s, save] = useDeviceSettings()
  const [status, setStatus] = useState<Status | null>(null)
  const [pairing, setPairing] = useState<Pairing | null>(null)
  const [done, setDone] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const sending = !!s.display.tablets?.[register.id]
  const setSending = (on: boolean) =>
    void save({ ...s, display: { ...s.display, tablets: { ...s.display.tablets, [register.id]: on } } })
  const address = `${window.location.origin}/display`
  const onlyThisPc = ['localhost', '127.0.0.1', '[::1]'].includes(window.location.hostname)

  const refresh = useCallback(() => api<Status>(`pos/display?register_id=${register.id}`), [register.id])

  useEffect(() => {
    let stopped = false
    let timer: ReturnType<typeof setTimeout> | undefined
    const tick = async () => {
      try {
        const next = await refresh()
        if (stopped) return
        setStatus(next)
        if (pairing && !next.code_pending) {
          setPairing(null)
          setDone(next.paired && Date.now() < pairing.until
            ? 'Tablet connected. Press Full screen on it and keep it on charge.'
            : 'That code expired. Press the button for a new one.')
        }
      } catch {
        if (!stopped) setStatus(null) // offline: say so rather than show an old status
      }
      if (!stopped) timer = setTimeout(tick, 3000)
    }
    void tick()
    return () => {
      stopped = true
      clearTimeout(timer)
    }
  }, [refresh, pairing])

  const connect = async () => {
    setError(null)
    setDone(null)
    setBusy(true)
    try {
      const r = await api<{ code: string; expires_in: number }>('pos/display/pair', { body: { register_id: register.id } })
      const next: Pairing = { code: r.code, link: `${address}#${r.code.replace('-', '')}`, until: Date.now() + r.expires_in * 1000 }
      try {
        const QRCode = (await import('qrcode')).default
        next.qr = await QRCode.toDataURL(next.link, { width: 220, margin: 2 })
      } catch {
        /* no QR: the code can still be typed */
      }
      setPairing(next)
      setSending(true)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const disconnect = async () => {
    setError(null)
    setDone(null)
    try {
      await api('pos/display/disconnect', { body: { register_id: register.id } })
      setSending(false)
      setPairing(null)
      setStatus({ paired: false, online: false, code_pending: false })
    } catch (e) {
      setError((e as Error).message)
    }
  }

  return (
    <div className="tablet-pair">
      <h4>Tablet (no cable)</h4>
      <p className="muted small">
        On the tablet open <span className="fig tablet-address">{address}</span> in Chrome. Any internet connection works.
      </p>
      {onlyThisPc && (
        <div className="alert alert-warn small">
          This POS is open on an address only this computer can reach, so a tablet can't open it. Open the POS from your
          site's web address, then connect the tablet.
        </div>
      )}
      <ErrorBox error={error} />
      {done && <div className="alert alert-info small" role="status">{done}</div>}
      {pairing && (
        <div className="tablet-code">
          {pairing.qr && <img src={pairing.qr} alt="QR code that connects the tablet" />}
          <p className="small">Scan this with the tablet's camera, or type this code on it. It works once, for 10 minutes.</p>
          <strong className="fig">{pairing.code}</strong>
        </div>
      )}
      {!status && <p className="muted small">Checking the tablet… (needs an internet connection)</p>}
      <div className="device-pair">
        {status?.paired && (
          <Badge tone={status.online ? 'good' : 'warn'}>{status.online ? 'Tablet showing' : 'Tablet not responding'}</Badge>
        )}
        {status && (
          <Button kind={status.paired || pairing ? 'default' : 'primary'} busy={busy} onClick={() => void connect()}>
            {pairing ? 'New code' : status.paired ? 'Replace tablet' : 'Connect a tablet'}
          </Button>
        )}
        {status?.paired && <Button kind="ghost" onClick={() => void disconnect()}>Disconnect</Button>}
      </div>
      {status?.paired && (
        <label className="check">
          <input type="checkbox" checked={sending} onChange={(e) => setSending(e.target.checked)} />
          Show this computer's bill on the tablet
        </label>
      )}
    </div>
  )
}
