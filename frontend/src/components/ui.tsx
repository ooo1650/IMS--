import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { api, cursorPath, type Page } from '../lib/api'

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

export function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="field">
      <span className="field-label">{label}</span>
      {children}
      {hint && <span className="field-hint">{hint}</span>}
    </label>
  )
}

export function Modal({ title, onClose, children, wide }: { title: string; onClose: () => void; children: ReactNode; wide?: boolean }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])
  return (
    <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className={`modal ${wide ? 'modal-wide' : ''}`} role="dialog" aria-label={title}>
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
