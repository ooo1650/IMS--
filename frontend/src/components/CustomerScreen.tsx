/**
 * What the customer sees, whichever way it arrives: a second window on the till
 * (pages/CustomerDisplay) or a paired tablet (pages/TabletDisplay).
 */
import { useEffect, useRef, type ReactNode } from 'react'
import type { DisplayState } from '../lib/devices/display'
import { Icon } from './Icon'

export function CustomerScreen({ s, footer }: { s: DisplayState | null; footer?: ReactNode }) {
  const list = useRef<HTMLUListElement>(null)
  // A long bill keeps the line just scanned in view.
  useEffect(() => {
    const el = list.current
    if (!el) return
    const toEnd = () => (el.scrollTop = el.scrollHeight)
    toEnd()
    if (!('ResizeObserver' in window)) return
    // Also when the QR loads or the tablet is turned and the list gets shorter.
    const seen = new ResizeObserver(toEnd)
    seen.observe(el)
    return () => seen.disconnect()
  }, [s])

  if (!s || s.mode === 'idle' || (s.mode === 'cart' && !s.lines.length)) {
    return (
      <div className="cd cd-idle">
        <span className="brand-mark cd-mark"><Icon name="layers" size={40} /></span>
        <h1>{s?.store || 'Welcome'}</h1>
        <p>Namaste — your items will appear here</p>
        {footer}
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
        <ul className="cd-lines" ref={list}>
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
