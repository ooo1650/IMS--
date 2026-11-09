import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { api, cursorPath, type Page } from '../lib/api'
import { moneyProblem, normalizeNumber, parseWhole } from '../lib/money'

export function useLoad<T>(fn: () => Promise<T>, deps: unknown[]) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const reload = useCallback(() => {
    setLoading(true)
    fn()
      .then((d) => {
        setData(d)
        setError(null)
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)
  useEffect(reload, [reload])
  return { data, error, loading, reload, setData }
}

/** Cursor-paginated list with "Load more". */
export function usePaged<T>(path: string | null) {
  const [items, setItems] = useState<T[]>([])
  const [next, setNext] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const load = useCallback(
    async (p: string, append: boolean) => {
      setLoading(true)
      try {
        const page = await api<Page<T>>(p)
        setItems((cur) => (append ? [...cur, ...page.results] : page.results))
        setNext(page.next)
        setError(null)
      } catch (e) {
        setError((e as Error).message)
      } finally {
        setLoading(false)
      }
    },
    [],
  )
  const reload = useCallback(() => {
    if (path) void load(path, false)
  }, [path, load])
  useEffect(reload, [reload])
  return { items, loading, error, reload, more: next ? () => load(cursorPath(next), true) : null }
}

export function Button({
  kind = 'default', busy, children, ...rest
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { kind?: 'default' | 'primary' | 'danger' | 'ghost'; busy?: boolean }) {
  return (
    <button {...rest} className={`btn btn-${kind} ${rest.className ?? ''}`} disabled={rest.disabled || busy}>
      {busy ? '…' : children}
    </button>
  )
}

/** A labelled input. `error` (shown in red, replacing the hint) says what's wrong with what was typed. */
export function Field({ label, hint, error, children }: { label: string; hint?: string; error?: string | null; children: ReactNode }) {
  return (
    <label className="field">
      <span className="field-label">{label}</span>
      {children}
      {error ? <span className="field-hint bad" role="alert">{error}</span> : hint && <span className="field-hint">{hint}</span>}
    </label>
  )
}

/**
 * Whole-number box for quantities. Unlike <input type="number">: the mouse
 * wheel can't change it, it can be emptied while typing (no jump back to 1, so
 * backspace + 5 gives 5, not 15), Nepali digits work, and decimals/letters are
 * refused as you type. The parent only ever gets a whole number in [min, max];
 * an empty or too-small box snaps back to `min` when you leave it. Focus
 * selects the number, so typing replaces it.
 */
export function QtyInput({ value, onChange, min = 0, max, className = 'input-sm', ...rest }: {
  value: number; onChange: (n: number) => void; min?: number; max?: number
} & Omit<React.InputHTMLAttributes<HTMLInputElement>, 'value' | 'onChange' | 'min' | 'max'>) {
  const [text, setText] = useState(String(value))
  useEffect(() => {
    setText((t) => (parseWhole(t) === value ? t : String(value)))
  }, [value])
  const take = (raw: string) => {
    // Digits only (and a leading minus when negatives are allowed); over max becomes max.
    let clean = normalizeNumber(raw).replace(min < 0 ? /(?!^-)\D/g : /\D/g, '')
    const typed = parseWhole(clean)
    if (typed != null && max != null && typed > max) clean = String(max)
    setText(clean)
    const n = parseWhole(clean)
    if (n != null && n >= min) onChange(n)
  }
  return (
    <input {...rest} className={className} inputMode="numeric" autoComplete="off" value={text}
      onFocus={(e) => { e.target.select(); rest.onFocus?.(e) }}
      onChange={(e) => take(e.target.value)}
      onBlur={(e) => {
        const n = parseWhole(text)
        if (n == null || n < min) {
          setText(String(min))
          onChange(min)
        }
        rest.onBlur?.(e)
      }} />
  )
}

/**
 * Rupee box: keeps what you type as text (so "12" can become "120.50" without
 * the cursor jumping), accepts "Rs 500", "500/-" and Nepali digits, and shows
 * why an amount isn't usable. `onChange` gets the text; parse with toPaisa().
 */
export function MoneyInput({ value, onChange, ...rest }: {
  value: string; onChange: (raw: string) => void
} & Omit<React.InputHTMLAttributes<HTMLInputElement>, 'value' | 'onChange'>) {
  // Focus selects what's there, so typing replaces a pre-filled amount (the
  // tendered box opens with the total) instead of appending to it.
  return <input {...rest} inputMode="decimal" autoComplete="off" value={value} aria-invalid={!!moneyProblem(value)}
    onFocus={(e) => { e.target.select(); rest.onFocus?.(e) }} onChange={(e) => onChange(e.target.value)} />
}

/**
 * Dialog. Once something has been typed into it, a stray click on the
 * backdrop no longer closes it (a half-filled form used to vanish), and Escape
 * asks first. The × button and the form's own Cancel close it as before.
 */
export function Modal({ title, onClose, children, wide }: { title: string; onClose: () => void; children: ReactNode; wide?: boolean }) {
  const edited = useRef(false)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      if (!edited.current || window.confirm('Close without saving? What you typed here will be lost.')) onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])
  return (
    <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && !edited.current && onClose()}>
      <div className={`modal ${wide ? 'modal-wide' : ''}`} role="dialog" aria-label={title} onInput={() => { edited.current = true }}>
        <div className="modal-head">
          <h2>{title}</h2>
          <button className="icon-btn" onClick={onClose} aria-label="Close">×</button>
        </div>
        <div className="modal-body">{children}</div>
      </div>
    </div>
  )
}

export function ErrorBox({ error }: { error: string | null | undefined }) {
  return error ? <div className="alert alert-error">{error}</div> : null
}

export function Badge({ tone = 'neutral', children }: { tone?: 'neutral' | 'good' | 'warn' | 'bad' | 'info'; children: ReactNode }) {
  return <span className={`badge badge-${tone}`}>{children}</span>
}

const STATUS_TONES: Record<string, 'neutral' | 'good' | 'warn' | 'bad' | 'info'> = {
  paid: 'good', received: 'good', finished: 'good', closed: 'neutral', success: 'good',
  open: 'info', ordered: 'info', in_transit: 'info', counting: 'info', partial: 'warn', draft: 'neutral',
  voided: 'bad', cancelled: 'bad', payment_orphan: 'bad', failed: 'bad', expired: 'bad',
}
// Normal, finished states read as plain text; a badge marks something to look at.
const QUIET_STATUSES = new Set(['paid', 'received', 'finished', 'closed', 'success', 'draft'])
export function Status({ value }: { value: string }) {
  const label = value.replace(/_/g, ' ')
  if (QUIET_STATUSES.has(value)) return <span className="status-quiet">{label.charAt(0).toUpperCase() + label.slice(1)}</span>
  return <Badge tone={STATUS_TONES[value] ?? 'neutral'}>{label}</Badge>
}

export function PageHead({ title, sub, children }: { title: string; sub?: string; children?: ReactNode }) {
  return (
    <div className="page-head">
      <div className="page-title">
        <h1>{title}</h1>
        {sub && <span className="page-sub">{sub}</span>}
      </div>
      <div className="page-actions">{children}</div>
    </div>
  )
}

export function LoadMore({ more, loading }: { more: (() => void) | null; loading: boolean }) {
  if (!more) return null
  return (
    <div className="load-more">
      <Button onClick={more} busy={loading}>Load more</Button>
    </div>
  )
}

export function fmtDate(iso: string | null | undefined, withTime = true) {
  if (!iso) return '—'
  const d = new Date(iso)
  return withTime
    ? d.toLocaleString('en-GB', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' })
    : d.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' })
}

export function Tabs<T extends string>({ value, onChange, tabs }: { value: T; onChange: (v: T) => void; tabs: [T, string][] }) {
  return (
    <div className="tabs" role="tablist">
      {tabs.map(([k, label]) => (
        <button key={k} role="tab" aria-selected={value === k} className={value === k ? 'tab active' : 'tab'} onClick={() => onChange(k)}>
          {label}
        </button>
      ))}
    </div>
  )
}

/** "How this page works": a short note at the top of a page. Hidden per
 *  browser once dismissed, and brought back with the small link it leaves. */
export function Guide({ id, title, children }: { id: string; title: string; children: ReactNode }) {
  const key = `ims.guide.${id}`
  const [open, setOpen] = useState(() => {
    try {
      return localStorage.getItem(key) !== 'hidden'
    } catch {
      return true
    }
  })
  const toggle = (v: boolean) => {
    setOpen(v)
    try {
      if (v) localStorage.removeItem(key)
      else localStorage.setItem(key, 'hidden')
    } catch {
      /* private mode: just this visit */
    }
  }
  if (!open) return <button type="button" className="link guide-reopen" onClick={() => toggle(true)}>How this page works</button>
  return (
    <aside className="guide" aria-label={title}>
      <div className="guide-head">
        <strong>{title}</strong>
        <button type="button" className="link" onClick={() => toggle(false)}>Hide</button>
      </div>
      <div className="guide-body">{children}</div>
    </aside>
  )
}
