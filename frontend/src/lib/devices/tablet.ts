/**
 * Till side of the tablet customer display: a separate device, so the screen
 * goes through the server (backend/sales/display.py). One request at a time,
 * newest screen wins, so a late reply can never put an older total back up.
 */
import { ApiError, api } from '../api'
import type { DisplayState } from './display'

export interface Till { branch: number; register: number }
type Post = (till: Till, state: DisplayState) => Promise<unknown>

/** Wait for a burst of scans to settle. */
const SETTLE_MS = 300
const RETRY_MS = 5000
/** The server blanks a bill the till hasn't repeated for 90 s. */
const REPEAT_MS = 20_000
/** The newest lines are the ones on screen; the total still covers the whole bill. */
const MAX_LINES = 100

export function forTablet(s: DisplayState): DisplayState {
  return { ...s, lines: s.lines.slice(-MAX_LINES).map((l) => ({ ...l, name: l.name.slice(0, 200) })) }
}

export function createTabletSender(post: Post) {
  const waiting = new Map<number, { till: Till; state: DisplayState }>()
  let till: Till | null = null
  let latest: DisplayState | null = null
  let shown = '' // the last screen queued
  let busy = false
  let timer: ReturnType<typeof setTimeout> | undefined
  let repeat: ReturnType<typeof setInterval> | undefined

  const schedule = (ms: number) => {
    timer ??= setTimeout(() => {
      timer = undefined
      void send()
    }, ms)
  }

  const queue = (t: Till, state: DisplayState) => {
    waiting.set(t.register, { till: t, state: forTablet(state) })
    if (!busy) schedule(SETTLE_MS)
  }

  async function send() {
    const next = waiting.values().next().value
    if (busy || !next) return
    waiting.delete(next.till.register)
    busy = true
    let delay = SETTLE_MS
    try {
      await post(next.till, next.state)
    } catch (e) {
      // A refusal won't improve by retrying; anything else (offline, 5xx, 429) may.
      if (e instanceof ApiError && e.status < 500 && e.status !== 429) shown = ''
      else {
        if (!waiting.has(next.till.register)) waiting.set(next.till.register, next)
        delay = RETRY_MS
      }
    }
    busy = false
    if (waiting.size) schedule(delay)
  }

  return {
    /** The till whose bill goes to its tablet, or null (POS left, till changed, switched off). */
    follow(next: Till | null) {
      if (till?.register === next?.register) return
      // Clear the till being left, so it doesn't keep showing this customer's bill.
      if (till) queue(till, { mode: 'idle', store: latest?.store ?? '', lines: [], total: '' })
      till = next
      clearInterval(repeat)
      if (!till) return
      if (latest) queue(till, latest)
      repeat = setInterval(() => {
        if (till && latest && latest.mode !== 'idle') queue(till, latest)
      }, REPEAT_MS)
    },
    show(state: DisplayState) {
      latest = state
      const key = JSON.stringify(state)
      if (key === shown) return
      shown = key
      if (till) queue(till, state)
    },
  }
}

export const tablet = createTabletSender((till, state) =>
  api('pos/display/state', { body: { register_id: till.register, state }, branch: till.branch }))
