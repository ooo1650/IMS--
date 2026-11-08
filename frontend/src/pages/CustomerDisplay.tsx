/**
 * Customer-facing screen. Opened from POS → Devices on a second monitor; it only
 * shows what the POS in this same browser publishes (BroadcastChannel), so it
 * needs no login and no server access.
 */
import { useEffect, useState } from 'react'
import { Icon } from '../components/Icon'
import { subscribeDisplay, type DisplayState } from '../lib/devices/display'

export default function CustomerDisplay() {
  const [s, setS] = useState<DisplayState | null>(null)
  useEffect(() => {
    document.title = 'Customer display'
    return subscribeDisplay(setS)
  }, [])

  if (!s || s.mode === 'idle' || (s.mode === 'cart' && !s.lines.length)) {
    return (
      <div className="cd cd-idle">
        <span className="brand-mark cd-mark"><Icon name="layers" size={40} /></span>
        <h1>{s?.store || 'Welcome'}</h1>
        <p>Namaste — your items will appear here</p>
      </div>
    )
  }

  if (s.mode === 'done') {
    return (
      <div className="cd cd-idle">
        <span className="cd-check"><Icon name="check" size={64} /></span>
        <h1>Thank you · धन्यवाद</h1>
        <p>Total paid Rs {s.total}</p>
        {s.change && <div className="cd-change">Change Rs {s.change}</div>}
      </div>
    )
  }

  return (
    <div className="cd">
      <header className="cd-head">
        <span className="brand-mark"><Icon name="layers" size={20} /></span>
        <strong>{s.store}</strong>
      </header>
      <div className="cd-body">
        <ul className="cd-lines">
          {s.lines.map((l, i) => (
            <li key={i} className={i === s.lines.length - 1 ? 'latest' : ''}>
              <span>{l.name}</span>
              <span className="muted">× {l.qty}</span>
              <strong>Rs {l.amount}</strong>
            </li>
          ))}
        </ul>
        <aside className="cd-total">
          {s.mode === 'pay' && s.qr ? (
            <>
              <div className="muted">Scan to pay with FonePay</div>
              <img src={s.qr} alt="FonePay QR" />
            </>
          ) : (
            <dl>
              {s.subtotal && <><dt>Subtotal</dt><dd>Rs {s.subtotal}</dd></>}
              {s.discount && <><dt>Discount</dt><dd>− Rs {s.discount}</dd></>}
              {s.tax && <><dt>VAT</dt><dd>Rs {s.tax}</dd></>}
            </dl>
          )}
          <div className="cd-grand"><span>Total</span><strong>Rs {s.total}</strong></div>
        </aside>
      </div>
    </div>
  )
}
