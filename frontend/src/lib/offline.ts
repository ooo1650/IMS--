/**
 * Offline storage for the POS (design doc §10.3, §15), in IndexedDB:
 *  - catalog: last synced sale-relevant product data per branch (no cost prices)
 *  - queue:   cash sales taken offline, waiting to sync (each with idempotency key)
 *  - kv:      cached login, POS config, and each register's local receipt-series copy
 *
 * A queued sale that never syncs (device lost / storage cleared) is a lost sale;
 * the printed receipt is the source of truth and the server flags series gaps.
 */
import { openDB, type DBSchema, type IDBPDatabase } from 'idb'
import type { Me, PosConfig, PosProduct } from './types'

export interface QueuedSale {
  idempotency_key: string
  branch_id: number
  register_id: number
  created_at: string
  receipt_seq: number
  fiscal_year: string
  payload: Record<string, unknown>
  /** Per-branch series: receipt_seq is a provisional-slip counter, not an invoice number. */
  provisional?: boolean
  status: 'pending' | 'error'
  error?: string
  attempts: number
}

export interface CachedLogin {
  username: string
  salt: string
  hash: string
  me: Me
  cached_at: number
}

interface PosDB extends DBSchema {
  kv: { key: string; value: unknown }
  catalog: { key: number; value: { branch_id: number; generated_at: string; products: PosProduct[] } }
  queue: { key: string; value: QueuedSale; indexes: { by_created: string } }
}

let dbp: Promise<IDBPDatabase<PosDB>> | null = null
function db() {
  dbp ??= openDB<PosDB>('ims-pos', 1, {
    upgrade(d) {
      d.createObjectStore('kv')
      d.createObjectStore('catalog', { keyPath: 'branch_id' })
      d.createObjectStore('queue', { keyPath: 'idempotency_key' }).createIndex('by_created', 'created_at')
    },
  })
  return dbp
}

export async function kvGet<T>(key: string): Promise<T | undefined> {
  return (await (await db()).get('kv', key)) as T | undefined
}
export async function kvSet(key: string, value: unknown) {
  await (await db()).put('kv', value, key)
}

export async function kvEntries<T>(prefix: string): Promise<[string, T][]> {
  const d = await db()
  const keys = (await d.getAllKeys('kv')).filter((k) => String(k).startsWith(prefix))
  return Promise.all(keys.map(async (k) => [String(k), (await d.get('kv', k)) as T] as [string, T]))
}
export async function kvDelete(key: string) {
  await (await db()).delete('kv', key)
}

// --- catalog ------------------------------------------------------------------
export async function saveCatalog(branchId: number, generatedAt: string, products: PosProduct[]) {
  await (await db()).put('catalog', { branch_id: branchId, generated_at: generatedAt, products })
}
export async function loadCatalog(branchId: number) {
  return (await db()).get('catalog', branchId)
}

export async function savePosConfig(cfg: PosConfig) {
  await kvSet(`posconfig:${cfg.branch.id}`, cfg)
}
export async function loadPosConfig(branchId: number) {
  return kvGet<PosConfig>(`posconfig:${branchId}`)
}

// --- receipt series (per register, §9.10) ----------------------------------
const seqKey = (branchId: number, registerId: number, fy: string) => `seq:${branchId}:${registerId}:${fy}`

/** Never move the local copy backwards. */
export async function syncLocalSeq(branchId: number, registerId: number, fy: string, serverNext: number) {
  const key = seqKey(branchId, registerId, fy)
  const cur = (await kvGet<number>(key)) ?? 1
  if (serverNext > cur) await kvSet(key, serverNext)
}
export async function peekLocalSeq(branchId: number, registerId: number, fy: string) {
  return (await kvGet<number>(seqKey(branchId, registerId, fy))) ?? 1
}

/**
 * Allocate the next local number and queue the sale in ONE IndexedDB
 * transaction, so a number is never used without its sale being stored.
 */
export async function queueOfflineSale(
  sale: Omit<QueuedSale, 'receipt_seq' | 'status' | 'attempts'>,
): Promise<QueuedSale> {
  const d = await db()
  const tx = d.transaction(['kv', 'queue'], 'readwrite')
  const key = sale.provisional
    ? `prov:${sale.branch_id}:${sale.register_id}`
    : seqKey(sale.branch_id, sale.register_id, sale.fiscal_year)
  const n = ((await tx.objectStore('kv').get(key)) as number | undefined) ?? 1
  const queued: QueuedSale = { ...sale, receipt_seq: n, status: 'pending', attempts: 0 }
  await tx.objectStore('kv').put(n + 1, key)
  await tx.objectStore('queue').put(queued)
  await tx.done
  return queued
}

export async function listQueue(): Promise<QueuedSale[]> {
  return (await db()).getAllFromIndex('queue', 'by_created')
}
export async function updateQueued(q: QueuedSale) {
  await (await db()).put('queue', q)
}
export async function removeQueued(key: string) {
  await (await db()).delete('queue', key)
}

/** Daily safety export of the unsynced queue (§10.3 local storage loss). */
export async function exportQueue(): Promise<Blob> {
  return new Blob([JSON.stringify(await listQueue(), null, 2)], { type: 'application/json' })
}

// --- offline login (§10.3 step 2) -------------------------------------------
const enc = new TextEncoder()
const hex = (buf: ArrayBuffer) => [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, '0')).join('')

async function pbkdf2(password: string, saltHex: string) {
  const salt = new Uint8Array(saltHex.match(/../g)!.map((h) => parseInt(h, 16)))
  const key = await crypto.subtle.importKey('raw', enc.encode(password), 'PBKDF2', false, ['deriveBits'])
  return hex(await crypto.subtle.deriveBits({ name: 'PBKDF2', hash: 'SHA-256', salt, iterations: 150_000 }, key, 256))
}

export async function cacheLogin(username: string, password: string, me: Me) {
  if (!crypto?.subtle) return // needs a secure context (https or localhost)
  const salt = hex(crypto.getRandomValues(new Uint8Array(16)).buffer)
  const login: CachedLogin = { username: username.toLowerCase(), salt, hash: await pbkdf2(password, salt), me, cached_at: Date.now() }
  await kvSet(`login:${login.username}`, login)
  await kvSet('login:last', login.username)
}

export async function offlineLogin(username: string, password: string, maxAgeHours: number): Promise<Me> {
  const login = await kvGet<CachedLogin>(`login:${username.toLowerCase()}`)
  if (!login) throw new Error('This user has never logged in on this device while online.')
  if (Date.now() - login.cached_at > maxAgeHours * 3600_000)
    throw new Error(`Cached login is older than ${maxAgeHours}h — reconnect to log in.`)
  if ((await pbkdf2(password, login.salt)) !== login.hash) throw new Error('Invalid username or password')
  await kvSet('login:last', login.username)
  return login.me
}

export async function lastCachedLoginAge(): Promise<number | null> {
  const user = await kvGet<string>('login:last')
  if (!user) return null
  const login = await kvGet<CachedLogin>(`login:${user}`)
  return login ? Date.now() - login.cached_at : null
}
