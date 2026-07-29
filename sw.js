// Service worker minimal de Sortclip.
// Objectif : rendre le site « installable » (ajout à l'écran d'accueil) et
// offrir un repli hors-ligne léger. On NE met PAS en cache agressivement les
// pages applicatives (mes-clips, tarifs…) ni les appels réseau : le contenu
// dynamique (Supabase, Modal, Stripe) doit toujours passer par le réseau.
const CACHE = 'sortclip-v1';
const SHELL = ['/index.html', '/nav.css', '/icon-192.png', '/icon-512.png'];

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).catch(() => {}));
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  // On ne touche qu'aux GET de même origine ; tout le reste (API, POST,
  // Supabase, Stripe, Modal, polices) passe directement par le réseau.
  if (req.method !== 'GET' || new URL(req.url).origin !== self.location.origin) {
    return;
  }
  // Réseau d'abord (contenu à jour), repli sur le cache si hors-ligne.
  event.respondWith(
    fetch(req)
      .then((res) => {
        const copy = res.clone();
        caches.open(CACHE).then((c) => c.put(req, copy)).catch(() => {});
        return res;
      })
      .catch(() => caches.match(req).then((r) => r || caches.match('/index.html')))
  );
});
