import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError, NetworkError } from '../api'
import type { DisplayState } from './display'
import { createTabletSender, forTablet } from './tablet'

const till = { branch: 1, register: 7 }
const bill = (total: string): DisplayState => ({ mode: 'cart', store: 'S', total, lines: [{ name: 'Milk', qty: 1, amount: total }] })
const idle: DisplayState = { mode: 'idle', store: 'S', total: '', lines: [] }

describe('tablet sender', () => {
  beforeEach(() => void vi.useFakeTimers())
  afterEach(() => void vi.useRealTimers())

  const make = (impl: () => Promise<unknown> = async () => ({})) => {
    const post = vi.fn<(t: typeof till, s: DisplayState) => Promise<unknown>>(impl)
    const sender = createTabletSender(post)
    return { post, sender, totals: () => post.mock.calls.map((c) => c[1].total) }
  }

  it('sends nothing until a tablet is in use', async () => {
    const { post, sender } = make()
    sender.show(bill('1.00'))
    await vi.advanceTimersByTimeAsync(1000)
    expect(post).not.toHaveBeenCalled()
  })

  it('sends only the last of a burst of scans', async () => {
    const { sender, totals } = make()
    sender.follow(till)
    for (const t of ['1.00', '2.00', '3.00']) sender.show(bill(t))
    await vi.advanceTimersByTimeAsync(1000)
    expect(totals()).toEqual(['3.00'])
  })

  it('never has two requests out at once', async () => {
    let finish = () => {}
    const { sender, totals } = make(() => new Promise<void>((r) => (finish = r)))
    sender.follow(till)
    sender.show(bill('1.00'))
    await vi.advanceTimersByTimeAsync(400)
    sender.show(bill('2.00'))
    sender.show(bill('3.00'))
    await vi.advanceTimersByTimeAsync(2000)
    expect(totals()).toEqual(['1.00'])
    finish()
    await vi.advanceTimersByTimeAsync(400)
    expect(totals()).toEqual(['1.00', '3.00'])
  })

  it('retries after a network failure with the newest bill', async () => {
    let first = true
    const { sender, totals } = make(async () => {
      if (first) {
        first = false
        throw new NetworkError('offline')
      }
    })
    sender.follow(till)
    sender.show(bill('1.00'))
    await vi.advanceTimersByTimeAsync(400)
    sender.show(bill('2.00'))
    await vi.advanceTimersByTimeAsync(6000)
    expect(totals()).toEqual(['1.00', '2.00'])
  })

  it('does not hammer a refusal', async () => {
    const { post, sender } = make(async () => {
      throw new ApiError(403, { detail: 'no' })
    })
    sender.follow(till)
    sender.show(idle)
    await vi.advanceTimersByTimeAsync(30_000)
    expect(post).toHaveBeenCalledTimes(1)
  })

  it('repeats an open bill but not the welcome screen', async () => {
    const { post, sender } = make()
    sender.follow(till)
    sender.show(bill('1.00'))
    await vi.advanceTimersByTimeAsync(41_000)
    expect(post.mock.calls.length).toBe(3)
    sender.show(idle)
    await vi.advanceTimersByTimeAsync(60_000)
    expect(post.mock.calls.length).toBe(4)
  })

  it('clears the till it leaves, on that till\'s branch', async () => {
    const { post, sender } = make()
    sender.follow(till)
    sender.show(bill('1.00'))
    await vi.advanceTimersByTimeAsync(400)
    sender.follow(null)
    await vi.advanceTimersByTimeAsync(400)
    expect(post.mock.lastCall?.[0]).toEqual(till)
    expect(post.mock.lastCall?.[1].mode).toBe('idle')
  })

  it('a remount (React StrictMode) sends the bill, not a blank', async () => {
    const { sender, totals } = make()
    sender.show(bill('1.00'))
    sender.follow(till)
    sender.follow(null)
    sender.follow({ ...till })
    await vi.advanceTimersByTimeAsync(1000)
    expect(totals()).toEqual(['1.00'])
  })

  it('keeps the newest lines of a huge bill', () => {
    const lines = Array.from({ length: 150 }, (_, i) => ({ name: `P${i}`, qty: 1, amount: '1.00' }))
    expect(forTablet({ ...bill('150.00'), lines }).lines.map((l) => l.name)).toEqual(lines.slice(50).map((l) => l.name))
  })
})
