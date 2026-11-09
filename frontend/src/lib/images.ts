/**
 * Product photos on the till.
 *
 * Photo URLs are content-addressed (a new photo gets a new URL), so a cached
 * copy is never stale. warmImageCache copies every catalog thumbnail into
 * Cache Storage during idle time, at low priority; the service worker (sw.js)
 * answers /media/ from that cache, so tiles show photos instantly and offline.
 * Nothing here is awaited by the cart or checkout.
 */

/** Must match MEDIA_CACHE in public/sw.js. */
const MEDIA_CACHE = 'ims-media-v1'

const idle = () =>
  new Promise<void>((resolve) =>
    'requestIdleCallback' in window ? requestIdleCallback(() => resolve(), { timeout: 2000 }) : setTimeout(resolve, 50),
  )

/** Download missing thumbnails (2 at a time, in idle time) and drop ones no longer in the catalog. */
export async function warmImageCache(urls: string[], signal: AbortSignal) {
  if (!('caches' in window)) return // needs a secure context (https or localhost)
  const cache = await caches.open(MEDIA_CACHE)
  const want = new Set(urls.map((u) => new URL(u, location.origin).href))
  const have = new Set<string>()
  for (const req of await cache.keys()) {
    // Full-size photos the service worker cached while browsing stay; stale thumbnails go.
    if (req.url.endsWith('-t.webp') && !want.has(req.url)) await cache.delete(req)
    else have.add(req.url)
  }
  const todo = [...want].filter((u) => !have.has(u))
  const worker = async () => {
    for (let url = todo.pop(); url && !signal.aborted; url = todo.pop()) {
      await idle()
      if (signal.aborted) return
      try {
        const res = await fetch(url, { credentials: 'same-origin', signal, priority: 'low' } as RequestInit)
        if (res.ok) await cache.put(url, res)
      } catch {
        return // offline or aborted: try again next time the catalog loads
      }
    }
  }
  await Promise.all([worker(), worker()])
}

/** Ask the browser not to evict IndexedDB (offline sales queue, catalog) or cached photos under storage pressure. */
export function requestPersistentStorage() {
  void navigator.storage?.persist?.().catch(() => undefined)
}

/**
 * Shrink a camera photo before upload (phones produce 5–10 MB files; a slow
 * branch link shouldn't have to carry them). The server re-encodes anyway.
 */
export async function downscaleImage(file: File, maxPx = 1600): Promise<Blob> {
  if (!('createImageBitmap' in window)) return file
  const bmp = await createImageBitmap(file, { imageOrientation: 'from-image' })
  const scale = Math.min(1, maxPx / Math.max(bmp.width, bmp.height))
  if (scale === 1 && file.size < 1_500_000) {
    bmp.close()
    return file
  }
  const canvas = document.createElement('canvas')
  canvas.width = Math.round(bmp.width * scale)
  canvas.height = Math.round(bmp.height * scale)
  const ctx = canvas.getContext('2d')!
  ctx.fillStyle = '#fff' // JPEG has no transparency
  ctx.fillRect(0, 0, canvas.width, canvas.height)
  ctx.drawImage(bmp, 0, 0, canvas.width, canvas.height)
  bmp.close()
  return new Promise((resolve) => canvas.toBlob((b) => resolve(b ?? file), 'image/jpeg', 0.88))
}
