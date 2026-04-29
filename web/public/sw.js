const CACHE_NAME = 'product-search-cache-v1';
const STATIC_ASSETS = [
  '/',
  '/manifest.webmanifest'
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll(STATIC_ASSETS);
    })
  );
});

self.addEventListener('fetch', (event) => {
  const request = event.request;
  
  // Exclude API requests or external requests from network-first HTML caching if needed
  // For navigation requests (HTML), use Network First
  if (request.mode === 'navigate' || request.headers.get('accept').includes('text/html')) {
    event.respondWith(
      fetch(request)
        .then((response) => {
          const responseClone = response.clone();
          caches.open(CACHE_NAME).then((cache) => {
            cache.put(request, responseClone);
          });
          return response;
        })
        .catch(() => caches.match(request))
    );
    return;
  }

  // For static assets, use Stale-While-Revalidate
  event.respondWith(
    caches.match(request).then((cachedResponse) => {
      const fetchPromise = fetch(request).then((networkResponse) => {
        caches.open(CACHE_NAME).then((cache) => {
          cache.put(request, networkResponse.clone());
        });
        return networkResponse;
      }).catch(() => {
        // Ignore network errors for static assets if offline
      });

      return cachedResponse || fetchPromise;
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
      // Open a new window or focus an existing one
      if (clients.openWindow) {
        return clients.openWindow(urlToOpen);
      }
    })
  );
});
