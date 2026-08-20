// Service Worker de ECOLTURA - notificaciones push

self.addEventListener('install', () => {
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(self.clients.claim());
});

// Recibe la notificación push enviada desde el servidor y la muestra
self.addEventListener('push', (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch (e) {
    data = { title: 'ECOLTURA', body: event.data ? event.data.text() : '' };
  }

  const title = data.title || 'ECOLTURA';
  const options = {
    body: data.body || '',
    icon: '/static/img/apple-touch-icon.jpeg',
    badge: '/static/img/apple-touch-icon.jpeg',
    data: { url: data.url || '/' }
  };

  event.waitUntil(self.registration.showNotification(title, options));
});

// Al tocar la notificación, abre (o enfoca) la app en la URL indicada
self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const url = event.notification.data?.url || '/';

  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clientList) => {
      for (const client of clientList) {
        if (client.url.includes(url) && 'focus' in client) {
          return client.focus();
        }
      }
      if (self.clients.openWindow) {
        return self.clients.openWindow(url);
      }
    })
  );
});
