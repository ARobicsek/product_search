// v2 — strict static-only caching.
//
// v1 used stale-while-revalidate for every same-origin GET, including
// Next.js's RSC payload fetch that fires from `router.refresh()` after a
// Run-now completes. The user saw "Done. Loading new report…" then the
// OLD report contents because the SW returned the cached RSC payload
// immediately and only refreshed the cache in the background. Hence
// the recurring "stale screen after run" complaint.
//
// v2 fix: only cache true static assets (images/css/js/fonts/manifest)
// and pass everything else (HTML navigations, RSC payloads, /api/*,
// cross-origin) straight through to network with no caching. We bump
// CACHE_NAME so v1 entries are evicted on activate, and use
// skipWaiting + clients.claim() so existing tabs adopt v2 on the first
// reload after deploy without needing to be force-closed.

const CACHE_NAME = 'product-search-cache-v2';
const STATIC_ASSET_RE = /\.(?:png|jpg|jpeg|svg|webp|gif|ico|css|js|mjs|woff2?|ttf|otf|map)(?:\?.*)?$/i;
const STATIC_PATH_PREFIXES = ['/_next/static/'];

self.addEventListener('install', (event) => {
  // Take over from the v1 SW immediately so users on stale tabs
  // pick up v2 on their next navigation.
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    Promise.all([
      // Drop v1 (and any future older caches) so stale RSC/HTML
      // entries can't surface again.
      caches.keys().then((keys) =>
        Promise.all(
          keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k))
        )
      ),
      self.clients.claim(),
    ])
  );
});

function isStaticAsset(url) {
  if (url.origin !== self.location.origin) return false;
  if (STATIC_PATH_PREFIXES.some((p) => url.pathname.startsWith(p))) return true;
  return STATIC_ASSET_RE.test(url.pathname);
}

self.addEventListener('fetch', (event) => {
  const request = event.request;
  if (request.method !== 'GET') return;

  const url = new URL(request.url);

  // Anything that isn't a true static asset (HTML pages, Next.js RSC
  // payloads, /api/*, cross-origin) goes straight to the network with
  // no SW involvement. The browser still gets normal HTTP caching
  // behavior driven by Cache-Control headers from the server.
  if (!isStaticAsset(url)) return;

  // Stale-while-revalidate is fine for static assets — they have
  // content-hashed URLs in /_next/static/, and other public assets
  // (icons, manifest) change rarely enough that one stale render is
  // not user-visible.
  event.respondWith(
    caches.match(request).then((cached) => {
      const fetchPromise = fetch(request)
        .then((networkResponse) => {
          if (networkResponse && networkResponse.status === 200) {
            const clone = networkResponse.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(request, clone));
          }
          return networkResponse;
        })
        .catch(() => undefined);
      return cached || fetchPromise;
    })
  );
});

self.addEventListener('push', (event) => {
  if (event.data) {
    let data = {};
    try {
      data = event.data.json();
    } catch (e) {
      data = { body: event.data.text() };
    }
    const title = data.title || 'Product Search Alert';
    const options = {
      body: data.body || 'A material change was detected.',
      icon: '/icon-192x192.png',
      data: {
        url: data.url || '/'
      }
    };
    event.waitUntil(self.registration.showNotification(title, options));
  }
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const urlToOpen = event.notification.data?.url || '/';

  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then((windowClients) => {
      if (clients.openWindow) {
        return clients.openWindow(urlToOpen);
      }
    })
  );
});
