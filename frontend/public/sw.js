/* POS service worker (§10.3 step 1): serves the app shell from cache when
 * offline. API calls are never cached — the app falls back to IndexedDB.
 * Product photos (/media/) are cache-first from MEDIA_CACHE, which
 * src/lib/images.ts fills in the background; their URLs are content hashes,
 * so a cached photo is never stale. */
const CACHE = 'ims-shell-v1'
const MEDIA_CACHE = 'ims-media-v1'
const SHELL = ['/', '/index.html', '/manifest.webmanifest', '/favicon.svg']

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()))
})

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE && k !== MEDIA_CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  )
})

self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url)
  if (e.request.method !== 'GET' || url.origin !== location.origin) return
  if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/django-admin/')) return

  if (url.pathname.startsWith('/media/')) {
    e.respondWith(
      caches.open(MEDIA_CACHE).then((c) =>
        c.match(e.request).then(
          (hit) =>
            hit ||
            fetch(e.request).then((res) => {
              if (res.ok) c.put(e.request, res.clone())
              return res
            }),
        ),
      ),
    )
    return
  }

  if (e.request.mode === 'navigate') {
    // Network first for the page; fall back to the cached shell (SPA routes).
    e.respondWith(
      fetch(e.request)
        .then((res) => {
          const copy = res.clone()
          caches.open(CACHE).then((c) => c.put('/index.html', copy))
          return res
        })
        .catch(() => caches.match('/index.html')),
    )
    return
  }
  // Hashed build assets: cache first, then network and remember.
  e.respondWith(
    caches.match(e.request).then(
      (hit) =>
        hit ||
        fetch(e.request).then((res) => {
          if (res.ok) {
            const copy = res.clone()
            caches.open(CACHE).then((c) => c.put(e.request, copy))
          }
          return res
        }),
    ),
  )
})
