import { beforeEach, describe, expect, it, vi } from 'vitest'

// sync.ts touches window/navigator at import time.
vi.stubGlobal('navigator', { onLine: true })
vi.stubGlobal('window', { addEventListener: () => {} })

const store = vi.hoisted(() => ({
  queue: [] as Array<Record<string, unknown>>,
  kv: new Map<string, unknown>(),
  updated: [] as Array<Record<string, unknown>>,
  removed: [] as string[],
}))

vi.mock('./offline', () => ({
  listQueue: async () => store.queue,
  removeQueued: async (k: string) => void store.removed.push(k),
  updateQueued: async (q: Record<string, unknown>) => void store.updated.push(q),
  syncLocalSeq: async () => {},
  kvEntries: async (prefix: string) => [...store.kv.entries()].filter(([k]) => k.startsWith(prefix)),
  kvDelete: async (k: string) => void store.kv.delete(k),
}))

const apiMock = vi.hoisted(() => ({ fn: vi.fn() }))
vi.mock('./api', async () => {
  class ApiError extends Error {
    status: number
    data: Record<string, unknown>
    constructor(status: number, data: Record<string, unknown>) {
      super(String(data.detail ?? ''))
      this.status = status
      this.data = data
    }
  }
  class NetworkError extends Error {}
  return { ApiError, NetworkError, api: (...a: unknown[]) => apiMock.fn(...a) }
})

const { syncQueue } = await import('./sync')
const { ApiError } = await import('./api')

const sale = (key: string) => ({
  idempotency_key: key, branch_id: 1, register_id: 1, created_at: '2026-11-09T10:00:00Z', receipt_seq: 1,
  fiscal_year: '2083-84', payload: {}, status: 'pending', attempts: 0,
})

describe('syncQueue', () => {
  beforeEach(() => {
    store.queue = []
    store.kv.clear()
    store.updated = []
    store.removed = []
    apiMock.fn.mockReset()
  })

  it('a 403 on one sale does not hold up the sales behind it', async () => {
    store.queue = [sale('a'), sale('b')]
    apiMock.fn.mockImplementation(async (_path: string, opts: { body: { idempotency_key: string } }) => {
      if (opts.body.idempotency_key === 'a') throw new ApiError(403, { detail: 'Register does not belong to the active branch' })
      return { receipt_seq: 1, fiscal_year: '2083-84', receipt_gaps: [] }
    })
    await syncQueue()
    expect(store.updated.map((q) => [q.idempotency_key, q.status])).toEqual([['a', 'error']])
    expect(store.removed).toEqual(['b'])
  })

  it('a 401 stops the loop and keeps every sale pending', async () => {
    store.queue = [sale('a'), sale('b')]
    apiMock.fn.mockRejectedValue(new ApiError(401, { detail: 'Not signed in' }))
    await syncQueue()
    expect(store.updated).toEqual([])
    expect(store.removed).toEqual([])
  })

  it('keeps an offline shift close when the server errors', async () => {
    store.kv.set('shiftclose:7', { shift_id: 7, counted_cash_paisa: 5000 })
    apiMock.fn.mockRejectedValue(new ApiError(500, { detail: 'Server error' }))
    await syncQueue()
    expect(store.kv.has('shiftclose:7')).toBe(true)
  })

  it('drops an offline shift close that was already closed elsewhere', async () => {
    store.kv.set('shiftclose:7', { shift_id: 7, counted_cash_paisa: 5000 })
    apiMock.fn.mockRejectedValue(new ApiError(400, { detail: 'Shift is already closed' }))
    await syncQueue()
    expect(store.kv.has('shiftclose:7')).toBe(false)
  })
})
