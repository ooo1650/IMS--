import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, api, formatWait } from './api'

vi.stubGlobal('document', { cookie: '' })

function respond(status: number, body: string, headers: Record<string, string> = {}) {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(body, { status, headers })))
}

async function failure(): Promise<ApiError> {
  try {
    await api('anything')
  } catch (e) {
    return e as ApiError
  }
  throw new Error('expected the request to fail')
}

describe('api errors', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('reads code and retry_after from a JSON 429', async () => {
    respond(429, JSON.stringify({ detail: 'Too many failed sign-ins. Try again in 15 minutes.', code: 'locked', retry_after: 900 }))
    const e = await failure()
    expect([e.status, e.code, e.retryAfter, e.message]).toEqual([429, 'locked', 900, 'Too many failed sign-ins. Try again in 15 minutes.'])
  })

  it('turns an HTML error page into a plain sentence and uses Retry-After', async () => {
    respond(429, '<html><head><title>429 Too Many Requests</title></head></html>', { 'Retry-After': '5' })
    const e = await failure()
    expect(e.message).toBe('Too many requests. Wait a few seconds and try again.')
    expect(e.retryAfter).toBe(5)
    expect(e.message).not.toContain('<')
  })

  it('explains a server crash without markup', async () => {
    respond(500, '<h1>Server Error (500)</h1>')
    expect((await failure()).message).toMatch(/^Something went wrong on the server/)
  })

  it('keeps code and retry_after out of field-error text', async () => {
    respond(400, JSON.stringify({ code: ['This field is required.'] }))
    // A serializer field literally named "code" (the 6-digit box) is still shown.
    expect((await failure()).message).toBe('Code: This field is required.')
  })

  it('names fields in words and numbers the bad rows', async () => {
    respond(400, JSON.stringify({ sale_price_paisa: ["Can't be negative."], lines: [{}, { qty: ['Must be at least 1.'] }] }))
    expect((await failure()).message).toBe("Sale price: Can't be negative. · Line 2, Quantity: Must be at least 1.")
  })

  it('leaves non-field errors unprefixed', async () => {
    respond(400, JSON.stringify({ non_field_errors: ['Supplier and product must belong to this business.'] }))
    expect((await failure()).message).toBe('Supplier and product must belong to this business.')
  })
})

describe('formatWait', () => {
  it('shows seconds, then minutes and seconds', () => {
    expect(formatWait(42)).toBe('42 s')
    expect(formatWait(125)).toBe('2 min 05 s')
  })
})
