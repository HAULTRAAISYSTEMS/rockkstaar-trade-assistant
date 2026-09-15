const CACHE_NAME = 'tradestaar-shell-v10';
const SHELL_ASSETS = [
  '/static/logo.png',
  '/static/icon-192.png',
  '/static/icon-512.png',
  '/static/apple-touch-icon.png',
  '/static/favicon-32.png',
  '/static/css/style.css',
  '/static/css/haultra.css',
  '/static/css/quick_mode.css',
  '/static/css/mobile.css',
  '/static/js/main.js',
  '/static/js/ws_manager.js'
];

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(CACHE_NAME).then((cache) => cache.addAll(SHELL_ASSETS)));
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(
      keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key))
    ))
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  if (event.request.method !== 'GET') return;
  const requestUrl = new URL(event.request.url);
  if (requestUrl.origin !== self.location.origin) return;

  if (requestUrl.pathname.startsWith('/static/')) {
    event.respondWith(
      fetch(event.request).then((response) => {
        const copy = response.clone();
        caches.open(CACHE_NAME).then((cache) => cache.put(event.request, copy));
        return response;
      }).catch(() => caches.match(event.request))
    );
    return;
  }

  if (event.request.mode === 'navigate') {
    event.respondWith(
      fetch(event.request).catch(() => new Response(
        '<!doctype html><html lang="en"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="theme-color" content="#060708"><title>Tradestaar Elite — Offline</title><style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#060708;color:#f5f1e8;font-family:system-ui;text-align:center;padding:24px;box-sizing:border-box}.card{max-width:420px;padding:32px;border:1px solid #4e3b17;border-radius:20px;background:#0d0e10}h1{color:#d6a63f;font-size:24px}p{color:#aaa}button{margin-top:14px;padding:12px 20px;border:0;border-radius:10px;background:#d6a63f;color:#080808;font-weight:800}</style><div class="card"><h1>Tradestaar Elite</h1><p>You are offline. Live market data requires an internet connection.</p><button onclick="location.reload()">Try again</button></div>',
        { headers: { 'Content-Type': 'text/html; charset=utf-8' }, status: 503 }
      ))
    );
  }
});

self.addEventListener('push', (event) => {
  let payload = {};
  try { payload = event.data ? event.data.json() : {}; } catch (_) { payload = {}; }
  const title = payload.title || 'Tradestaar research alert';
  const options = {
    body: payload.body || 'A material change matched one of your saved theses.',
    icon: '/static/icon-192.png?v=5',
    badge: '/static/favicon-32.png?v=5',
    tag: payload.tag || 'tradestaar-research',
    data: { url: payload.url || '/opportunity' }
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const requested = new URL((event.notification.data || {}).url || '/opportunity', self.location.origin);
  const destination = requested.origin === self.location.origin ? requested.href : new URL('/opportunity', self.location.origin).href;
  event.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clients) => {
    for (const client of clients) {
      if (client.url.startsWith(self.location.origin) && 'focus' in client) {
        if ('navigate' in client) return client.navigate(destination).then(() => client.focus());
        return client.focus();
      }
    }
    return self.clients.openWindow ? self.clients.openWindow(destination) : undefined;
  }));
});
