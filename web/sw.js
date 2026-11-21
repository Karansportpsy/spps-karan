/* SPPS service worker: shows content-free push notifications and opens the right page when one is tapped.
   It caches nothing and never reads or stores records. */
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()));

self.addEventListener('push', (event) => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch (e) { data = {}; }
  const title = typeof data.title === 'string' && data.title ? data.title.slice(0, 80) : 'SPPS';
  const body = typeof data.body === 'string' ? data.body.slice(0, 160) : '';
  const url = typeof data.url === 'string' ? data.url : '/';
  event.waitUntil(self.registration.showNotification(title, {
    body, tag: typeof data.kind === 'string' ? data.kind.slice(0, 60) : 'spps', renotify: true,
    icon: '/assets/spps-v1-logo.png', data: { url },
  }));
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  let target = '/';
  try {
    const u = new URL((event.notification.data && event.notification.data.url) || '/', self.location.origin);
    if (u.origin === self.location.origin) target = u.href;   // never follow a link to another site
  } catch (e) { /* keep the default */ }
  event.waitUntil((async () => {
    const all = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    for (const c of all) { if (c.url === target && 'focus' in c) return c.focus(); }
    return self.clients.openWindow(target);
  })());
});
