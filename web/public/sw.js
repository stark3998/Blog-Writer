/*
 * Blog Writer service worker (Phase 1 stub).
 *
 * It makes the app installable and takes control of open pages, nothing more:
 * no caching and no fetch handler, so every request still goes to the network
 * and the server stays the source of truth. Web Push handlers arrive with the
 * notifications work; notification payloads carry only a title and a link.
 *
 * Serve this file with `Cache-Control: no-cache` so updates are picked up.
 */
self.addEventListener('install', function () {
  self.skipWaiting();
});

self.addEventListener('activate', function (event) {
  event.waitUntil(self.clients.claim());
});
