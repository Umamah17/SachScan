/**
 * SachScan Progressive Web App Service Worker
 * Sovereign Cache & Offline Verification Engine
 * Version: 1.0.0
 */

const CACHE_NAME = 'sachscan-v2.0.0';

const PRECACHE_ASSETS = [
  '/',
  '/index.html',
  '/integrations.html',
  '/technology.html',
  '/help.html',
  '/login.html',
  '/offline.html',
  '/manifest.json',
  '/style.css',
  '/transitions.js',
  '/app.js',
  '/pwa.js',
  '/sachscan-api.js',
  '/integrations.config.js',
  '/download__4_-removebg-preview.png',
  '/icons/icon-32.png',
  '/icons/icon-64.png',
  '/icons/icon-128.png',
  '/icons/icon-192.png',
  '/icons/icon-256.png',
  '/icons/icon-384.png',
  '/icons/icon-512.png',
  '/icons/icon-maskable-192.png',
  '/icons/icon-maskable-512.png',
  '/whatsapp.png',
  '/telegram.png',
  '/gmail.png',
  '/discord.jpg',
  '/threads.jpg',
  '/x.jpg',
  '/reddit.png',
  '/logo-whatsapp.png',
  '/logo-telegram.png',
  '/logo-gmail.png',
  '/logo-discord.svg',
  '/logo-threads.svg',
  '/logo-x.svg',
  '/logo-reddit.svg',
  '/logo-instagram.svg'
];

// 1. Install Event: Pre-cache core shell
self.addEventListener('install', (event) => {
  self.skipWaiting();
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      console.log('[SachScan PWA] Pre-caching core platform shell...');
      return Promise.allSettled(
        PRECACHE_ASSETS.map((url) =>
          cache.add(url).catch((err) => {
            console.warn(`[SachScan PWA] Optional asset cache miss: ${url}`, err);
          })
        )
      );
    })
  );
});

// 2. Activate Event: Clean up outdated caches
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((cacheNames) => {
      return Promise.all(
        cacheNames.map((name) => {
          if (name !== CACHE_NAME) {
            console.log(`[SachScan PWA] Purging outdated cache: ${name}`);
            return caches.delete(name);
          }
        })
      );
    }).then(() => {
      return self.clients.claim();
    })
  );
});

// 3. Fetch Event: Intelligent Strategy
self.addEventListener('fetch', (event) => {
  const request = event.request;

  // Ignore non-GET requests or non-http(s) schemes (e.g. chrome-extension://)
  if (request.method !== 'GET' || !request.url.startsWith('http')) {
    return;
  }

  const url = new URL(request.url);

  // Never cache cross-origin calls (Supabase auth, CDNs handle their own caching) or API data
  if (url.origin !== self.location.origin) {
    return;
  }
  if (/^\/(api|conversations|chat|check|link|me|health|share-target|webhooks|integrations\/gmail)(\/|$)/.test(url.pathname)) {
    return;
  }

  // A. Navigation Requests (HTML Pages): Network-First with Cache & Offline Fallback
  if (request.mode === 'navigate') {
    event.respondWith(
      fetch(request)
        .then((networkResponse) => {
          if (networkResponse && networkResponse.status === 200) {
            const responseClone = networkResponse.clone();
            caches.open(CACHE_NAME).then((cache) => {
              cache.put(request, responseClone);
            });
          }
          return networkResponse;
        })
        .catch(async () => {
          const cachedResponse = await caches.match(request);
          if (cachedResponse) return cachedResponse;
          if (url.pathname === '/' || url.pathname === '/index.html') {
            const indexCached = await caches.match('/index.html');
            if (indexCached) return indexCached;
          }
          const offlinePage = await caches.match('/offline.html');
          return offlinePage || new Response('Offline mode active. Please reconnect.', {
            headers: { 'Content-Type': 'text/plain' }
          });
        })
    );
    return;
  }

  // B. Large image frame files in animation sequence: Cache-First
  if (url.pathname.includes('/ezgif-1dab58a0391d528b-jpg/')) {
    event.respondWith(
      caches.match(request).then((cachedResponse) => {
        if (cachedResponse) return cachedResponse;
        return fetch(request).then((networkResponse) => {
          if (networkResponse && networkResponse.status === 200) {
            const responseClone = networkResponse.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(request, responseClone));
          }
          return networkResponse;
        });
      })
    );
    return;
  }

  // C. Script & Style Assets (JS, CSS): Network-First for instant live code updates
  if (url.pathname.endsWith('.js') || url.pathname.endsWith('.css')) {
    event.respondWith(
      fetch(request)
        .then((networkResponse) => {
          if (networkResponse && networkResponse.status === 200) {
            const responseClone = networkResponse.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(request, responseClone));
          }
          return networkResponse;
        })
        .catch(() => caches.match(request))
    );
    return;
  }

  // D. Static Platform Assets (Icons, Logos, Images): Stale-While-Revalidate
  event.respondWith(
    caches.match(request).then((cachedResponse) => {
      const fetchPromise = fetch(request)
        .then((networkResponse) => {
          if (networkResponse && networkResponse.status === 200) {
            const responseClone = networkResponse.clone();
            caches.open(CACHE_NAME).then((cache) => {
              cache.put(request, responseClone);
            });
          }
          return networkResponse;
        })
        .catch(() => {});

      return cachedResponse || fetchPromise;
    })
  );
});

// 4. Message Event: Allow client to trigger immediate activation
self.addEventListener('message', (event) => {
  if (event.data && event.data.type === 'SKIP_WAITING') {
    self.skipWaiting();
  }
});
