// Medusa companion service worker.
//
// Offline is not a nicety here, it is the normal case. When the phone is
// joined to the sensor's own SoftAP there is no internet at all: the network
// the app is "online" on routes to one ESP32 and nothing else. An app that
// only loads when it can reach a server would be unusable in exactly the
// situation it exists for.
//
// So: the app shell is precached at install and served cache-first. The
// device itself is never cached — those are live radio operations and a stale
// answer about what the hardware is doing right now is worse than an error.

const CACHE = 'medusa-shell-v1';

// Everything needed to boot the UI with no network.
const SHELL = ['./', './index.html', './manifest.webmanifest', './favicon.svg'];

/**
 * Find the hashed bundle URLs by reading index.html.
 *
 * Vite renames the JS and CSS on every build, so a hardcoded list goes stale
 * silently — and the failure mode is nasty: the HTML shell is cached, the page
 * "loads" offline, and then renders nothing because the script it points at
 * was never cached. Caching the shell without its bundles is worse than not
 * caching at all, because it looks like it worked.
 */
async function discoverAssets() {
  try {
    const response = await fetch('./index.html', { cache: 'no-store' });
    if (!response.ok) return [];
    const html = await response.text();
    const urls = new Set();
    // Deliberately a regex and not DOMParser: DOMParser is not available in a
    // service worker.
    for (const match of html.matchAll(/<script[^>]+src="([^"]+)"/g)) urls.add(match[1]);
    for (const match of html.matchAll(/<link[^>]+href="([^"]+\.css)"/g)) urls.add(match[1]);
    return [...urls];
  } catch {
    return [];
  }
}

self.addEventListener('install', (event) => {
  event.waitUntil(
    (async () => {
      const cache = await caches.open(CACHE);
      const assets = await discoverAssets();
      // Individually, so one 404 does not abandon the whole install.
      await Promise.all(
        [...SHELL, ...assets].map((url) => cache.add(url).catch(() => {
          // A missing entry degrades offline support; it must not block
          // activation, or a single renamed file bricks the installed app.
        })),
      );
      await self.skipWaiting();
    })(),
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    (async () => {
      const names = await caches.keys();
      await Promise.all(names.filter((name) => name !== CACHE).map((name) => caches.delete(name)));
      await self.clients.claim();
    })(),
  );
});

/** Anything that talks to the hardware or the bridge must never be cached. */
function isLiveRequest(url) {
  return (
    url.protocol === 'ws:' ||
    url.protocol === 'wss:' ||
    url.pathname.startsWith('/socket.io') ||
    url.pathname.startsWith('/capture/') ||
    url.pathname.startsWith('/artifact/') ||
    url.pathname.startsWith('/health')
  );
}

self.addEventListener('fetch', (event) => {
  const request = event.request;
  if (request.method !== 'GET') return;

  const url = new URL(request.url);
  if (isLiveRequest(url)) return; // straight to the network, never cached

  // Cross-origin requests are the device's own endpoints in practice; leave
  // them alone rather than caching another origin's responses.
  if (url.origin !== self.location.origin) return;

  event.respondWith(
    (async () => {
      const cached = await caches.match(request);
      if (cached) {
        // Refresh in the background so the next launch is current without
        // making this one wait for a network that may not exist.
        event.waitUntil(
          (async () => {
            try {
              const fresh = await fetch(request);
              if (fresh.ok) (await caches.open(CACHE)).put(request, fresh.clone());
            } catch {
              // Offline is the expected case on a sensor SoftAP.
            }
          })(),
        );
        return cached;
      }

      try {
        const response = await fetch(request);
        if (response.ok && response.type === 'basic') {
          (await caches.open(CACHE)).put(request, response.clone());
        }
        return response;
      } catch (error) {
        // A navigation with no cache entry and no network still has to render
        // something: fall back to the shell so the app can explain itself.
        if (request.mode === 'navigate') {
          const shell = await caches.match('./index.html');
          if (shell) return shell;
        }
        throw error;
      }
    })(),
  );
});
