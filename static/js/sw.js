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

  const promesas = [self.registration.showNotification(title, options)];

  // Bolita de notificación en el ícono de la app (si el navegador lo soporta)
  if ('setAppBadge' in self.navigator) {
    promesas.push(self.navigator.setAppBadge(1).catch(() => {}));
  }

  event.waitUntil(Promise.all(promesas));
});

// Al tocar la notificación, navega (o abre) la app en la URL indicada.
// Importante: si ya hay una ventana abierta, hay que NAVEGARLA a la URL
// nueva (client.navigate), no solo enfocarla — de lo contrario iOS trae
// la app al frente con el contenido viejo, ignorando el mensaje nuevo.
self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const url = event.notification.data?.url || '/';

  if ('clearAppBadge' in self.navigator) {
    self.navigator.clearAppBadge().catch(() => {});
  }

  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clientList) => {
      const cliente = clientList[0];
      if (cliente) {
        if ('navigate' in cliente) {
          return cliente.navigate(url).then((c) => c.focus());
        }
        return cliente.focus();
      }
      if (self.clients.openWindow) {
        return self.clients.openWindow(url);
      }
    })
  );
});
