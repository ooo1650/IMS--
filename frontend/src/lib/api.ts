/** Thin fetch wrapper: session cookie + CSRF header + active branch header. */

let activeBranchId: number | null = null
export function setActiveBranch(id: number | null) {
  activeBranchId = id
}
export function getActiveBranch() {
  return activeBranchId
}

export class ApiError extends Error {
  status: number
  data: Record<string, unknown>
  constructor(status: number, data: Record<string, unknown>) {
    super(errorMessage(data) || `Request failed (${status})`)
    this.status = status
    this.data = data
  }
}

/** Network failure (offline), as opposed to an HTTP error response. */
export class NetworkError extends Error {}

function errorMessage(data: Record<string, unknown>): string {
  if (!data || typeof data !== 'object') return ''
  if (typeof data.detail === 'string') return data.detail
  return Object.entries(data)
    .filter(([k]) => k !== 'short_lines')
    .map(([k, v]) => `${k}: ${Array.isArray(v) ? v.map(flat).join(' ') : flat(v)}`)
    .join('; ')
}
function flat(v: unknown): string {
  if (typeof v === 'string') return v
  if (Array.isArray(v)) return v.map(flat).join(' ')
  if (v && typeof v === 'object') return Object.values(v).map(flat).join(' ')
  return String(v)
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
  let data: unknown = null
  try {
    data = text ? JSON.parse(text) : null
  } catch {
    data = { detail: text.slice(0, 200) }
  }
  if (!res.ok) throw new ApiError(res.status, (data as Record<string, unknown>) ?? {})
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
