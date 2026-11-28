"""Chromium lifecycle and the isolation every render page gets.

Layers, outermost first (each is sufficient on its own for the content we render):

1. Job-level concurrency limit (:class:`RenderLimiter`): at most N jobs, so at most N browsers and
   N pages, exist at once. Memory is bounded by this, not by luck.
2. Chromium is launched *per job* with a minimal environment (no app secrets), a throwaway HOME/TMPDIR,
   its own sandbox (when the host allows it), a V8 heap cap, no DNS (``MAP * ~NOTFOUND``), a
   dead-end proxy and WebRTC confined to that proxy, so even a request that escaped interception
   could not leave the process.
3. Every context routes ``**/*`` to a guard that aborts everything. Chromium never routes ``data:``
   URLs, which are the only thing our documents reference. Each aborted URL is recorded. Playwright
   does not route WebSockets or beacons; layer 2 (the dead-end proxy) is what stops those.
4. Static pages (slides, OG, SVG rasterising) run with JavaScript disabled and a CSP of
   ``default-src 'none'`` plus ``data:`` images/fonts.
5. Content is plain text through Jinja2 autoescape, so documents contain no attacker markup at all.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import shutil
import sys
import tempfile
import time
import weakref
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from playwright.async_api import Browser, BrowserContext, Page, Playwright, Route, async_playwright
from playwright.async_api import Error as PlaywrightError

from .errors import RenderError
from .settings import RenderSettings
from .telemetry import StageTimer

log = logging.getLogger("render.browser")

#: Proxy that nothing listens on. Playwright also forces loopback through it (``<-loopback>``), so no
#: request can reach the app's own port or any other local service.
DEAD_END_PROXY = "http://127.0.0.1:9"

_HARDENING_ARGS = (
    "--host-resolver-rules=MAP * ~NOTFOUND",  # no name resolves: no DNS prefetch or DNS exfiltration
    # WebRTC sends UDP (STUN) straight to IP literals: the route guard, the proxy, DNS and CSP never
    # see it. Forcing it through the (dead-end) proxy leaves it nothing to send from the JS page.
    "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
    "--no-pings",
    "--disable-domain-reliability",
    "--disable-sync",
    "--disable-notifications",
    "--deny-permission-prompts",
    "--mute-audio",
    "--disable-speech-api",
    "--disable-crash-reporter",
)


@dataclass(frozen=True, slots=True)
class BlockedRequest:
    url: str
    resource_type: str

    @property
    def redacted(self) -> str:
        """Scheme and host only: safe for logs (no paths, no query strings)."""
        parts = urlsplit(self.url)
        return f"{parts.scheme}://{parts.hostname or ''}"


@dataclass  # no slots: Playwright tags handler objects with an attribute
class RequestGuard:
    """Aborts every routed request and records it. Installed on every context we create."""

    blocked: list[BlockedRequest] = field(default_factory=list)

    async def __call__(self, route: Route) -> None:
        request = route.request
        if request.url.startswith("data:"):  # pragma: no cover - Chromium does not route data: URLs
            await route.continue_()
            return
        entry = BlockedRequest(url=request.url, resource_type=request.resource_type)
        self.blocked.append(entry)
        log.warning("render_request_blocked", extra={"target": entry.redacted, "type": entry.resource_type})
        await route.abort("blockedbyclient")


class RenderLimiter:
    """Job-level semaphore with in-flight telemetry. One instance per process (per event loop)."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self._sem = asyncio.Semaphore(limit)
        self.in_flight = 0
        self.max_in_flight = 0
        self.completed = 0

    @contextlib.asynccontextmanager
    async def slot(self) -> AsyncGenerator[None]:
        async with self._sem:
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
            try:
                yield
            finally:
                self.in_flight -= 1
                self.completed += 1


_LIMITERS: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, RenderLimiter] = weakref.WeakKeyDictionary()
HOME_PREFIX = "bw-render-"


def default_limiter(settings: RenderSettings) -> RenderLimiter:
    """The process-wide limiter for the running event loop."""
    loop = asyncio.get_running_loop()
    limiter = _LIMITERS.get(loop)
    if limiter is None:
        limiter = _LIMITERS[loop] = RenderLimiter(settings.max_concurrent_jobs)
    return limiter


def sweep_stale_homes(max_age_s: float = 3600.0, root: Path | None = None) -> int:
    """Delete per-job Chromium homes left behind by a SIGKILL (crash-only: call at start-up)."""
    base = root or Path(tempfile.gettempdir())
    removed = 0
    cutoff = time.time() - max_age_s
    for path in base.glob(f"{HOME_PREFIX}*"):
        try:
            if path.is_dir() and path.stat().st_mtime < cutoff:
                shutil.rmtree(path, ignore_errors=True)
                removed += 1
        except OSError:
            continue
    return removed


def minimal_env(home: Path) -> dict[str, str | float | bool]:
    """The complete environment Chromium gets: nothing inherited from the app (no secrets)."""
    env: dict[str, str | float | bool] = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": str(home),
        "TMPDIR": str(home),
        "LANG": "C.UTF-8",
        "TZ": "UTC",
    }
    if sys.platform.startswith("linux"):
        env["XDG_CONFIG_HOME"] = str(home / ".config")
        env["XDG_CACHE_HOME"] = str(home / ".cache")
    return env


def chromium_args(settings: RenderSettings) -> list[str]:
    js_flags = f"--max-old-space-size={settings.js_heap_limit_mb}"
    if settings.js_jitless:
        js_flags += " --jitless"
    return [*_HARDENING_ARGS, f"--js-flags={js_flags}"]


class RenderSession:
    """One render job: one Chromium launched for it, torn down with it.

    Pass ``playwright`` to reuse a long-lived Playwright driver; otherwise the session starts and stops
    its own (crash-only, nothing outlives the job).
    """

    def __init__(self, settings: RenderSettings | None = None, playwright: Playwright | None = None) -> None:
        self.settings = settings or RenderSettings.from_env()
        self._external_pw = playwright
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._home: Path | None = None
        self.guard = RequestGuard()
        self.timer = StageTimer()

    @property
    def browser(self) -> Browser:
        if self._browser is None:
            raise RenderError("render session is not open")
        return self._browser

    async def __aenter__(self) -> RenderSession:
        self._home = Path(tempfile.mkdtemp(prefix=HOME_PREFIX))
        try:
            with self.timer.stage("driver_start"):
                if self._external_pw is not None:
                    self._pw = self._external_pw
                else:
                    self._pw = await async_playwright().start()
            with self.timer.stage("browser_launch"):
                self._browser = await self._launch()
        except BaseException:
            await self._teardown()
            raise
        return self

    async def _launch(self) -> Browser:
        if self._pw is None or self._home is None:
            raise RenderError("render session is not open")
        try:
            return await self._pw.chromium.launch(
                headless=True,
                chromium_sandbox=self.settings.chromium_sandbox,
                env=minimal_env(self._home),
                args=chromium_args(self.settings),
                proxy={"server": DEAD_END_PROXY},
                timeout=self.settings.action_timeout_ms,
            )
        except PlaywrightError as exc:
            if "sandbox" in str(exc).lower():
                raise RenderError(
                    "Chromium could not start its sandbox on this host (user namespaces blocked?). "
                    "Only set BW_RENDER_CHROMIUM_SANDBOX=0 after reviewing the compensating controls."
                ) from exc
            raise RenderError("Chromium failed to launch") from exc

    async def __aexit__(self, *exc: object) -> None:
        await self._teardown()

    async def _teardown(self) -> None:
        if self._browser is not None:
            with contextlib.suppress(Exception):
                await self._browser.close()
            self._browser = None
        if self._pw is not None and self._external_pw is None:
            with contextlib.suppress(Exception):
                await self._pw.stop()
        self._pw = None
        if self._home is not None:
            shutil.rmtree(self._home, ignore_errors=True)
            self._home = None

    async def new_context(
        self,
        *,
        width: int,
        height: int,
        scale: float = 1.0,
        javascript: bool = False,
    ) -> BrowserContext:
        """A fresh, isolated context with the deny-all route guard installed."""
        ctx = await self.browser.new_context(
            java_script_enabled=javascript,
            viewport={"width": width, "height": height},
            device_scale_factor=scale,
            service_workers="block",
            accept_downloads=False,
            locale="en-US",
            timezone_id="UTC",
            color_scheme="light",
            reduced_motion="reduce",
            forced_colors="none",
        )
        ctx.set_default_timeout(self.settings.action_timeout_ms)
        await ctx.route("**/*", self.guard)
        ctx.on("page", _close_popups)
        return ctx

    @contextlib.asynccontextmanager
    async def static_page(self, *, width: int, height: int, scale: float = 1.0) -> AsyncGenerator[Page]:
        """A JavaScript-disabled page in its own context, closed (with its context) afterwards."""
        ctx = await self.new_context(width=width, height=height, scale=scale, javascript=False)
        try:
            page = await ctx.new_page()
            yield page
        finally:
            with contextlib.suppress(Exception):
                await ctx.close()


async def _close_popups(page: Page) -> None:
    # Our documents never open windows; anything that does is closed immediately.
    opener = await page.opener()
    if opener is not None:
        await page.close()


async def wait_for_fonts(page: Page) -> None:
    """Block until every web font used by the document has loaded (works with JS disabled:
    Playwright evaluates in a DevTools world the page's own scripts cannot touch)."""
    status = await page.evaluate("document.fonts.ready.then(() => document.fonts.status)")
    if status != "loaded":
        raise RenderError(f"fonts did not load (status={status})")


FIT_CHECK_JS = """() => {
  const out = [];
  for (const el of document.querySelectorAll('[data-fit]')) {
    const dy = el.scrollHeight - el.clientHeight;
    const dx = el.scrollWidth - el.clientWidth;
    if (dy > 1 || dx > 1) {
      const slide = el.closest('[data-slide]');
      out.push({slide: slide ? Number(slide.dataset.slide) : 0, box: el.dataset.fit, dx, dy});
    }
  }
  return out;
}"""


async def find_overflows(page: Page) -> list[str]:
    """Describe every ``[data-fit]`` box whose content is larger than the box."""
    raw: list[dict[str, object]] = await page.evaluate(FIT_CHECK_JS)
    return [f"slide {r['slide']} {r['box']} (+{r['dy']}px tall, +{r['dx']}px wide)" for r in raw]
