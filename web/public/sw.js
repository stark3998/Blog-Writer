/*
 * Blog Writer service worker.
 *
 * It makes the app installable, takes control of open pages and shows Web Push notifications.
 * There is no caching and no fetch handler: every request still goes to the network and the
 * server stays the source of truth.
 *
 * Push payloads are `{"title", "url", "tag"}` (server `notify.payload.NotificationPayload`):
 * a title of at most 80 characters and a same-origin path, nothing else. A notification shows
 * the title only; a tap opens the path, which leads to a page that asks before it changes
 * anything (GET never changes state). Anything that does not match is shown as a generic
 * notification that opens Today, so a malformed push can never point the app elsewhere.
 *
 * Serve this file with `Cache-Control: no-cache` so updates are picked up.
 */
'use strict';

var APP_NAME = 'Blog Writer';
var FALLBACK_PATH = '/today';
var MAX_TITLE = 80;
var MAX_PATH = 512;
var TAG_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$/;
// One leading "/" (never "//" or "/\", which browsers read as another origin), then URL characters.
var PATH_PATTERN = /^\/(?![/\\])[A-Za-z0-9\-._~!$&'()*+,;=:@/%?#]*$/;

/** Control characters, and the bidirectional overrides and isolates that make text read differently. */
function hasUnsafeCharacter(text) {
  for (var index = 0; index < text.length; index += 1) {
    var code = text.charCodeAt(index);
    if (code < 0x20 || (code >= 0x7f && code <= 0x9f)) return true;
    if ((code >= 0x202a && code <= 0x202e) || (code >= 0x2066 && code <= 0x2069)) return true;
  }
  return false;
}

/** The title to show, or the app's name when the payload's title is missing or unsafe. */
function safeTitle(value) {
  if (typeof value !== 'string') return APP_NAME;
  var title = value.trim();
  if (title === '' || Array.from(title).length > MAX_TITLE || hasUnsafeCharacter(title)) return APP_NAME;
  return title;
}

/** A same-origin path to open, or Today when the payload's path is missing or unsafe. */
function safePath(value) {
  if (typeof value !== 'string' || value.length > MAX_PATH || !PATH_PATTERN.test(value)) return FALLBACK_PATH;
  try {
    var target = new URL(value, self.location.origin);
    if (target.origin !== self.location.origin) return FALLBACK_PATH;
    return target.pathname + target.search + target.hash;
  } catch {
    return FALLBACK_PATH;
  }
}

/** Reads the push payload without ever throwing: a broken one still shows a notification. */
function readPayload(event) {
  if (!event.data) return {};
  try {
    var body = event.data.json();
    return body && typeof body === 'object' && !Array.isArray(body) ? body : {};
  } catch {
    return {};
  }
}

/** The notification for a push: title, a link to open, and a tag that replaces older ones. */
function notificationFor(payload) {
  var options = {
    data: { url: safePath(payload.url) },
    icon: '/icons/icon-192.png',
  };
  if (typeof payload.tag === 'string' && TAG_PATTERN.test(payload.tag)) options.tag = payload.tag;
  return { title: safeTitle(payload.title), options: options };
}

self.addEventListener('install', function () {
  self.skipWaiting();
});

self.addEventListener('activate', function (event) {
  event.waitUntil(self.clients.claim());
});

self.addEventListener('push', function (event) {
  var notification = notificationFor(readPayload(event));
  event.waitUntil(self.registration.showNotification(notification.title, notification.options));
});

/** Focuses an open window of the app and takes it to `path`, or opens a new one. */
function openPath(path) {
  var url = new URL(path, self.location.origin).href;
  return self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(function (windows) {
    for (var index = 0; index < windows.length; index += 1) {
      var client = windows[index];
      if (new URL(client.url).origin !== self.location.origin) continue;
      return client
        .focus()
        .then(function (focused) {
          var target = focused || client;
          // Only a window this worker controls can be navigated; another one just gets focus.
          return typeof target.navigate === 'function' ? target.navigate(url) : target;
        })
        .catch(function () {
          return self.clients.openWindow ? self.clients.openWindow(url) : undefined;
        });
    }
    return self.clients.openWindow ? self.clients.openWindow(url) : undefined;
  });
}

self.addEventListener('notificationclick', function (event) {
  event.notification.close();
  var data = event.notification.data;
  event.waitUntil(openPath(safePath(data && typeof data === 'object' ? data.url : undefined)));
});
