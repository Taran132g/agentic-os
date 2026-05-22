// Minimal service worker — enables PWA "Add to Home Screen" on Android/Chrome
// iOS uses apple-mobile-web-app-capable meta tags instead, so this is a bonus
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(clients.claim()));
// No caching — all requests pass through to the network (live WebSocket data)
self.addEventListener('fetch', e => e.respondWith(fetch(e.request)));
