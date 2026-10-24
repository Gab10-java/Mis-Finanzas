// Service worker: red primero (las actualizaciones SÍ llegan) y caché solo como respaldo sin conexión.
const VERSION = "billetera-v4";
const BASE = ["./", "index.html", "style.css", "app.js", "planificacion.js", "navegacion.js", "manifest.json", "ico/icon-192.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(VERSION).then((c) => c.addAll(BASE)));
  self.skipWaiting();
});

self.addEventListener("activate", (e) => {   // borra cachés de versiones anteriores
  e.waitUntil(caches.keys()
    .then((ks) => Promise.all(ks.filter((k) => k !== VERSION).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", (e) => {
  const u = new URL(e.request.url);
  if (e.request.method !== "GET" || u.origin !== location.origin || u.pathname.startsWith("/api/")) return;
  e.respondWith(fetch(e.request)
    .then((r) => { const copia = r.clone(); caches.open(VERSION).then((c) => c.put(e.request, copia)); return r; })
    .catch(() => caches.match(e.request).then((r) => r || caches.match("index.html"))));
});

// Al tocar un aviso de vencimiento, vuelve a la app (o la abre).
self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  e.waitUntil(clients.matchAll({ type: "window" }).then((ws) => (ws[0] ? ws[0].focus() : clients.openWindow("/"))));
});
