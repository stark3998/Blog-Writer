/*
 * Applies the saved theme before first paint, so a dark-mode reload never
 * flashes white. Loaded as a blocking same-origin script (CSP script-src 'self').
 * Keep in sync with src/theme/theme.ts: storage key, values and theme colours.
 */
(function () {
  var root = document.documentElement;
  var preference = 'system';
  try {
    preference = window.localStorage.getItem('bw:theme') || 'system';
  } catch {
    // Storage can be unavailable (private mode, policy). Fall back to the system theme.
  }
  var dark =
    preference === 'dark' ||
    (preference !== 'light' && window.matchMedia('(prefers-color-scheme: dark)').matches);
  root.classList.toggle('dark', dark);
  root.style.colorScheme = dark ? 'dark' : 'light';
  var metas = document.querySelectorAll('meta[name="theme-color"]');
  for (var i = 0; i < metas.length; i += 1) {
    metas[i].setAttribute('content', dark ? '#14171e' : '#ffffff');
  }
})();
