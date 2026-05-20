// WPOM Service Worker
// Handles push notifications from the notify-coach-push Edge Function.
//
// Lifecycle: registered by index.html on every page load.
// Activate immediately so updates ship without waiting for tab close.

self.addEventListener('install', function(event) {
  self.skipWaiting();
});

self.addEventListener('activate', function(event) {
  event.waitUntil(self.clients.claim());
});

// Push received from server
self.addEventListener('push', function(event) {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch (e) {
    data = { title: 'WPOM', body: event.data ? event.data.text() : 'New activity on your program' };
  }
  const title = data.title || 'New WPOM Interest';
  const options = {
    body: data.body || 'An athlete has signaled interest in your program.',
    icon: '/wpom_logo.png',
    badge: '/wpom_logo.png',
    tag: data.tag || 'wpom-interest',
    renotify: true,
    requireInteraction: false,
    data: {
      url: data.url || 'https://wpomwrestling.com/'
    }
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

// Click handler: open WPOM in foreground tab or create one
self.addEventListener('notificationclick', function(event) {
  event.notification.close();
  const targetUrl = (event.notification.data && event.notification.data.url) || 'https://wpomwrestling.com/';
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(function(clientList) {
      for (let i = 0; i < clientList.length; i++) {
        const client = clientList[i];
        if (client.url.indexOf('wpomwrestling.com') !== -1 && 'focus' in client) {
          client.navigate(targetUrl);
          return client.focus();
        }
      }
      if (self.clients.openWindow) {
        return self.clients.openWindow(targetUrl);
      }
    })
  );
});
