/** Thin fetch wrapper: session cookie + CSRF header + active branch / business headers. */

let activeBranchId: number | null = null
let activeOrgId: number | null = null
export function setActiveBranch(id: number | null) {
  activeBranchId = id
}
/** The active business (a user may work for several). The branch, when set, wins on the server. */
export function setActiveOrg(id: number | null) {
  activeOrgId = id
}

export class ApiError extends Error {
  status: number
  data: Record<string, unknown>
  /** Stable reason from the server (e.g. 'locked', 'otp_wait', 'login_expired', 'rate_limited', 'csrf'). */
  code: string | null
  /** Seconds before trying again is useful (429s), from the body or the Retry-After header. */
  retryAfter: number | null
  constructor(status: number, data: Record<string, unknown>, retryAfter: number | null = null) {
    super(errorMessage(data) || statusMessage(status))
    this.status = status
    this.data = data
    this.code = typeof data?.code === 'string' ? data.code : null
    const fromBody = Number(data?.retry_after)
    this.retryAfter = Number.isFinite(fromBody) && fromBody > 0 ? Math.ceil(fromBody) : retryAfter
  }
}

/** What to tell the user when the server gave no sentence of its own. */
export function statusMessage(status: number): string {
  if (status === 401) return "You're signed out. Sign in again to continue."
  if (status === 403) return "You don't have permission for this, or your session expired. Reload the page and try again."
  if (status === 404) return 'Not found. Reload the page; if it keeps happening, tell your administrator.'
  if (status === 413) return 'That file is too large: 10 MB at most.'
  if (status === 429) return 'Too many requests. Wait a few seconds and try again.'
  if (status >= 500) return 'Something went wrong on the server. Try again; if it keeps happening, tell your administrator.'
  return `The request didn't work (error ${status}). Try again.`
}

/** "45 s", "2 min 05 s": for countdowns on buttons. */
export function formatWait(seconds: number): string {
  const s = Math.max(0, Math.ceil(seconds))
  if (s < 60) return `${s} s`
  return `${Math.floor(s / 60)} min ${String(s % 60).padStart(2, '0')} s`
}

/** Network failure (offline), as opposed to an HTTP error response. */
export class NetworkError extends Error {}

/** Field names in server errors -> words on the screen. */
const LABELS: Record<string, string> = {
  sale_price_paisa: 'Sale price', cost_price_paisa: 'Cost price', unit_cost_paisa: 'Cost', unit_price_paisa: 'Price',
  qty: 'Quantity', qty_sent: 'Quantity', qty_ordered: 'Quantity', qty_received: 'Quantity received', counted_qty: 'Count',
  counted_cash_paisa: 'Counted cash', opening_cash_paisa: 'Opening cash', tendered_paisa: 'Cash tendered',
  taxable_paisa: 'Amount', tax_rate: 'VAT rate', vat_rate: 'VAT rate', levy_rate: 'Levy rate', excise_rate: 'Excise rate',
  input_credit_blocked_pct: 'Blocked input credit', reorder_point: 'Reorder point', reorder_qty: 'Reorder quantity',
  pack_qty: 'Pack size', discount_value: 'Discount', sale_discount_value: 'Discount', sale_discount_reason: 'Discount reason',
  product: 'Product', product_id: 'Product', supplier: 'Supplier', sku: 'SKU', tax_id: 'PAN', buyer_pan: 'Buyer PAN',
  register_id: 'Register', sale_line_id: 'Item', customer_id: 'Customer', to_branch: 'Destination branch',
  effective_from: 'Effective from', abbreviated_invoice_limit_paisa: 'Abbreviated invoice limit',
  delivery_copy_limit_paisa: 'Delivery copy limit', cbms_realtime_window_minutes: 'Real-time window',
}
const ITEM: Record<string, string> = { lines: 'Line', barcodes: 'Barcode', rates: 'Rate', roles: 'Role' }
const SILENT = new Set(['detail', 'non_field_errors', 'lines', 'barcodes'])

export function fieldLabel(key: string): string {
  if (LABELS[key]) return LABELS[key]
  const words = key.replace(/_(paisa|bp|id|pct)$/, '').replace(/_/g, ' ').trim()
  return words.charAt(0).toUpperCase() + words.slice(1)
}

/** Server validation errors as sentences: "Line 2, Quantity: Must be at least 1." */
function describe(value: unknown, where: string[]): string[] {
  const prefix = where.filter(Boolean).join(', ')
  if (typeof value === 'string' || typeof value === 'number') return [prefix ? `${prefix}: ${value}` : String(value)]
  if (Array.isArray(value)) {
    if (value.every((v) => typeof v === 'string')) return value.length ? [prefix ? `${prefix}: ${value.join(' ')}` : value.join(' ')] : []
    return value.flatMap((v) => describe(v, where))
  }
  if (value && typeof value === 'object') {
    return Object.entries(value).flatMap(([k, v]) => {
      if (Array.isArray(v) && v.some((x) => x && typeof x === 'object' && !Array.isArray(x))) {
        // A list of rows (lines, barcodes): number the rows that have a problem.
        return v.flatMap((row, i) => (row && typeof row === 'object' && Object.keys(row).length
          ? describe(row, [...where, `${ITEM[k] ?? fieldLabel(k)} ${i + 1}`]) : []))
      }
      return describe(v, [...where, SILENT.has(k) ? '' : fieldLabel(k)])
    })
  }
  return []
}

function errorMessage(data: Record<string, unknown>): string {
  if (!data || typeof data !== 'object') return ''
  if (typeof data.detail === 'string') return data.detail
  // `code`/`retry_after` as metadata (a string / a number) aren't messages; a
  // field error on a field named "code" (a list) still is.
  const fields = Object.fromEntries(Object.entries(data).filter(([k, v]) =>
    k !== 'short_lines' && !(k === 'code' && typeof v === 'string') && !(k === 'retry_after' && typeof v === 'number')))
  return describe(fields, []).join(' · ')
}

function csrfToken(): string {
  return document.cookie.match(/(?:^|; )csrftoken=([^;]+)/)?.[1] ?? ''
}

export async function api<T = unknown>(
  path: string,
  opts: { method?: string; body?: unknown; form?: FormData; branch?: number } = {},
): Promise<T> {
  const method = opts.method ?? (opts.body || opts.form ? 'POST' : 'GET')
  const headers: Record<string, string> = {}
  const branch = opts.branch ?? activeBranchId
  if (branch) headers['X-Branch-Id'] = String(branch)
  if (activeOrgId) headers['X-Org-Id'] = String(activeOrgId)
  if (method !== 'GET') headers['X-CSRFToken'] = csrfToken()
  let body: BodyInit | undefined
  if (opts.form) body = opts.form
  else if (opts.body !== undefined) {
    headers['Content-Type'] = 'application/json'
    body = JSON.stringify(opts.body)
  }
  let res: Response
  try {
    res = await fetch(`/api/v1/${path.replace(/^\//, '')}`, { method, headers, body, credentials: 'same-origin' })
  } catch {
    throw new NetworkError('Network unavailable')
  }
  // Proxy up but backend unreachable: treat like being offline so cash sales keep working.
  if (res.status === 502 || res.status === 503 || res.status === 504) throw new NetworkError(`Server unavailable (${res.status})`)
  const text = await res.text()
  // A web page where JSON was expected: the /api forwarding in front of the
  // backend is wrong (e.g. a hosting rewrite serving the app's index.html).
  if (res.ok && /^\s*<!doctype html/i.test(text)) {
    throw new ApiError(502, { detail: 'The server sent a web page instead of data: the /api forwarding to the backend is misconfigured' })
  }
  let data: unknown = null
  try {
    data = text ? JSON.parse(text) : null
  } catch {
    // Not JSON: an HTML error page from a proxy in front of the server. Say
    // what the status means in plain words (statusMessage), never show markup.
    if (res.ok) throw new ApiError(502, { detail: 'The server sent something unexpected. Reload the page and try again.' })
    data = {}
  }
  const retryHeader = Number(res.headers.get('Retry-After'))
  if (!res.ok) throw new ApiError(res.status, (data as Record<string, unknown>) ?? {}, retryHeader > 0 ? retryHeader : null)
  return data as T
}

export interface Page<T> {
  results: T[]
  next: string | null
  previous: string | null
}

/** Follow a DRF cursor URL returned in Page.next */
export function cursorPath(url: string): string {
  return url.replace(/^.*\/api\/v1\//, '')
}

export const qs = (params: Record<string, string | number | boolean | null | undefined>) => {
  const p = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== '') p.set(k, String(v))
  const s = p.toString()
  return s ? `?${s}` : ''
}
