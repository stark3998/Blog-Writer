export const SERVICE_WORKER_URL = '/sw.js';

/** The error's name (for example "SecurityError"), never its message: messages can carry URLs. */
function errorName(error: unknown): string {
  if (typeof error === 'object' && error !== null && 'name' in error && typeof error.name === 'string') {
    return error.name;
  }
  return 'unknown';
}

interface RegisterOptions {
  /** Register only in production builds; the dev server has no service worker. */
  enabled: boolean;
  nav?: Navigator;
  win?: Window;
}

/**
 * Registers the service worker after the page has loaded, so it never competes
 * with first paint. Push subscription is not part of this stub.
 */
export function registerServiceWorker({ enabled, nav = navigator, win = window }: RegisterOptions): void {
  if (!enabled || !('serviceWorker' in nav)) return;

  const register = () => {
    nav.serviceWorker.register(SERVICE_WORKER_URL, { scope: '/' }).catch((error: unknown) => {
      // Not fatal: the app works without it. Logged without any page content.
      console.warn('Service worker registration failed', errorName(error));
    });
  };

  if (win.document.readyState === 'complete') register();
  else win.addEventListener('load', register, { once: true });
}
