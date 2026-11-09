import { lazy, type ComponentType } from 'react'

/**
 * Every screen is its own chunk, downloaded the first time it's needed.
 * `prefetchPage` starts that download early (menu hover/focus, idle time),
 * so the click itself doesn't wait on the network.
 */
const LOADERS: Record<string, () => Promise<{ default: ComponentType }>> = {
  '/pos': () => import('./Pos'),
  '/dashboard': () => import('./Dashboard'),
  '/reports': () => import('./BranchReport'),
  '/sales': () => import('./Sales'),
  '/products': () => import('./Products'),
  '/inventory': () => import('./Inventory'),
  '/transfers': () => import('./Transfers'),
  '/stocktakes': () => import('./Stocktakes'),
  '/purchasing': () => import('./Purchasing'),
  '/import': () => import('./Import'),
  '/admin': () => import('./Admin'),
  '/payments': () => import('./Payments'),
  '/compliance': () => import('./Compliance'),
  '/help': () => import('./Help'),
  '/account': () => import('./Account'),
}

export const PAGES: Record<string, ComponentType> = Object.fromEntries(
  Object.entries(LOADERS).map(([path, load]) => [path, lazy(load)]),
)

const started = new Set<string>()

/** Start downloading a screen's code (once). Safe to call often; failures are ignored (the click retries). */
export function prefetchPage(path: string) {
  const load = LOADERS[path]
  if (!load || started.has(path)) return
  started.add(path)
  load().catch(() => started.delete(path))
}

/** Prefetch when the browser is idle, so it never competes with what's on screen. */
export function prefetchWhenIdle(paths: string[]) {
  const run = () => paths.forEach(prefetchPage)
  if ('requestIdleCallback' in window) window.requestIdleCallback(run, { timeout: 5000 })
  else setTimeout(run, 2000)
}
