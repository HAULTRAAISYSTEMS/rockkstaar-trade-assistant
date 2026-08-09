const CACHE_NAME = 'tradestaar-shell-v5';
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
      caches.match(event.request).then((cached) => cached || fetch(event.request))
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
