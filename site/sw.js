/* Logan's Daily Newspaper — service worker.
   Registered from assets/app.js with a RELATIVE path ("./sw.js"), so the scope
   is wherever the site is served (GitHub Pages: /intelligence-terminal/).
   Strategy per spec:
     - cache-first  : ./assets/*  and  ./equations/*  (immutable-ish static files)
     - network-first: HTML documents and reports/index.json (always prefer fresh)
   Bump VERSION on any strategy/shell change; old caches are deleted on activate. */

/* v2 (2026-08-16): newspaper redesign + listen-to-text.
   The bump is not cosmetic -- /assets/* is cache-first, so an installed PWA
   would keep serving the OLD style.css and app.js forever without it. Bump
   VERSION on ANY change to a file under assets/. */
var VERSION = "ldn-cache-v2";

var SHELL = [
  "./",
  "./index.html",
  "./status.html",
  "./assets/style.css",
  "./assets/app.js",
  "./assets/report.js",
  "./reports/index.json",
  "./manifest.webmanifest"
];

self.addEventListener("install", function (event) {
  event.waitUntil(
    caches.open(VERSION).then(function (cache) {
      /* best-effort precache: one missing file must not fail the install */
      return Promise.all(SHELL.map(function (url) {
        return cache.add(url).catch(function () {});
      }));
    }).then(function () { return self.skipWaiting(); })
  );
});

self.addEventListener("activate", function (event) {
  event.waitUntil(
    caches.keys().then(function (keys) {
      return Promise.all(keys.filter(function (key) {
        return key !== VERSION;
      }).map(function (key) { return caches.delete(key); }));
    }).then(function () { return self.clients.claim(); })
  );
});

function isStatic(url) {
  return /\/(assets|equations)\//.test(url.pathname);
}

function isDocument(request, url) {
  return request.mode === "navigate" ||
    request.destination === "document" ||
    /\.html$/.test(url.pathname) ||
    /\/reports\/index\.json$/.test(url.pathname);
}

self.addEventListener("fetch", function (event) {
  var request = event.request;
  if (request.method !== "GET") return;
  var url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  /* cache-first: css/js/icons/equation images */
  if (isStatic(url)) {
    event.respondWith(
      caches.match(request).then(function (hit) {
        return hit || fetch(request).then(function (response) {
          if (response && response.ok) {
            var copy = response.clone();
            caches.open(VERSION).then(function (cache) { cache.put(request, copy); });
          }
          return response;
        });
      })
    );
    return;
  }

  /* network-first with cache fallback: pages + archive index */
  if (isDocument(request, url)) {
    event.respondWith(
      fetch(request).then(function (response) {
        if (response && response.ok) {
          var copy = response.clone();
          caches.open(VERSION).then(function (cache) { cache.put(request, copy); });
        }
        return response;
      }).catch(function () {
        return caches.match(request).then(function (hit) {
          /* offline and never cached: fall back to the archive index page */
          return hit || caches.match("./index.html");
        });
      })
    );
  }
  /* anything else (e.g. pagefind assets): default browser handling */
});
