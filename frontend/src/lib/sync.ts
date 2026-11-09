/**
 * Connectivity + queued offline-sale sync (§10.3 step 8).
 * Sales sync one at a time, oldest first, each with its idempotency key, so a
 * retry after a dropped response is safe. A sale the server rejects stays in
 * the queue marked 'error' for a manager — it is never silently dropped.
 */
import { useEffect, useState, useSyncExternalStore } from 'react'
import { ApiError, NetworkError, api } from './api'
import { kvDelete, kvEntries, listQueue, removeQueued, syncLocalSeq, updateQueued, type QueuedSale } from './offline'

type Listener = () => void
const listeners = new Set<Listener>()
let online = navigator.onLine
let syncing = false
let lastSyncReport: { synced: number; failed: number; gaps: number[]; at: number } | null = null

function emit() {
  listeners.forEach((l) => l())
}
export function setOnline(v: boolean) {
  if (online !== v) {
    online = v
    emit()
    if (v) void syncQueue()
  }
}
window.addEventListener('online', () => void probe())
window.addEventListener('offline', () => setOnline(false))

/** navigator.onLine lies on captive LANs; confirm by hitting the API. */
export async function probe(): Promise<boolean> {
  try {
    await api('auth/csrf')
    setOnline(true)
  } catch (e) {
    setOnline(!(e instanceof NetworkError))
  }
  return online
}

export function useOnline() {
  return useSyncExternalStore(
    (l) => {
      listeners.add(l)
      return () => listeners.delete(l)
    },
    () => online,
  )
}

export function useQueue() {
  const [queue, setQueue] = useState<QueuedSale[]>([])
  useEffect(() => {
    const load = () => void listQueue().then(setQueue)
    load()
    listeners.add(load)
    const t = setInterval(load, 5000)
    return () => {
      listeners.delete(load)
      clearInterval(t)
    }
  }, [])
  return queue
}

function provisionalRef(q: QueuedSale) {
  return String(q.payload.provisional_ref ?? `P-${q.register_id}-${String(q.receipt_seq).padStart(6, '0')}`)
}

export function getLastSyncReport() {
  return lastSyncReport
}

export async function syncQueue(): Promise<void> {
  if (syncing || !online) return
  syncing = true
  let synced = 0
  let failed = 0
  let gaps: number[] = []
  let failedShiftCloses = 0
  try {
    for (const q of await listQueue()) {
      if (q.status === 'error' && q.attempts >= 3) {
        failed++
        continue
      }
      try {
        const res = await api<{ receipt_seq: number; fiscal_year: string; receipt_gaps: number[] }>('pos/checkout/sync', {
          body: {
            ...q.payload, idempotency_key: q.idempotency_key, register_id: q.register_id, fiscal_year: q.fiscal_year,
            created_at: q.created_at,
            ...(q.provisional
              ? { receipt_seq: null, provisional_ref: provisionalRef(q) }
              : { receipt_seq: q.receipt_seq }),
          },
          branch: q.branch_id,
        })
        await removeQueued(q.idempotency_key)
        if (!q.provisional) await syncLocalSeq(q.branch_id, q.register_id, q.fiscal_year, res.receipt_seq + 1)
        gaps = res.receipt_gaps ?? []
        synced++
      } catch (e) {
        if (e instanceof NetworkError) {
          setOnline(false)
          break
        }
        // Signed out (e.g. after an offline login): keep everything pending and
        // retry once someone signs in again — don't burn the retry budget.
        if (e instanceof ApiError && e.status === 401) break
        // 403 is about this sale (register deactivated, role removed at that
        // branch): mark it and carry on, so it can't hold up the sales behind it.
        await updateQueued({ ...q, status: 'error', attempts: q.attempts + 1, error: e instanceof ApiError ? e.message : String(e) })
        failed++
      }
    }
    // Shifts closed while offline post after their sales (§10.3 step 5).
    if (online)
      for (const [key, close] of await kvEntries<{ shift_id: number; counted_cash_paisa: number }>('shiftclose:')) {
        try {
          await api(`pos/shifts/${close.shift_id}/close/`, { body: { counted_cash_paisa: close.counted_cash_paisa, offline: true } })
          await kvDelete(key)
        } catch (e) {
          if (e instanceof NetworkError) break
          if (e instanceof ApiError && e.status === 400 && /already closed/i.test(e.message)) {
            await kvDelete(key) // closed elsewhere; nothing left to post
            continue
          }
          // Anything else (signed out, server error): keep the count and retry later.
          failedShiftCloses++
        }
      }
  } finally {
    syncing = false
    if (synced || failed || failedShiftCloses) lastSyncReport = { synced, failed: failed + failedShiftCloses, gaps, at: Date.now() }
    emit()
  }
}

// Periodic heartbeat: re-probe when offline, flush the queue when online.
setInterval(() => {
  if (!online) void probe()
  else void syncQueue()
}, 15000)
