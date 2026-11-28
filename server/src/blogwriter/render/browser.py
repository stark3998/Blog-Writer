"""Chromium lifecycle and the isolation every render page gets.

Layers, outermost first (each is sufficient on its own for the content we render):

1. Job-level concurrency limit (:class:`RenderLimiter`): at most ``BW_RENDER_CONCURRENCY`` jobs,
   so at most that many drivers, browsers and pages exist at once. Memory is bounded by this.
2. The Playwright driver **and** Chromium are launched per job with a minimal environment (no app
   secrets: no ``IDENTITY_ENDPOINT``/``IDENTITY_HEADER``, no ``AZURE_*``, no ``BW_*``) and a
   throwaway ``HOME``/``TMPDIR`` that is deleted afterwards. Chromium also gets its own sandbox
   (when the host allows it), a V8 heap cap, ``--jitless``, no DNS (``MAP * ~NOTFOUND``), a
   dead-end proxy and WebRTC confined to that proxy, so even a request that escaped interception
   could not leave the process.
3. Every context routes ``**/*`` to a guard that aborts everything. Chromium never routes
   ``data:`` URLs, which are the only thing our documents reference. Playwright does not route
   WebSockets or beacons; layer 2 (the dead-end proxy) is what stops those.
4. Static pages (slides, cards, OG images, SVG rasterising) run with JavaScript disabled and a CSP
   of ``default-src 'none'`` plus ``data:`` images and fonts. Only the Mermaid page runs script,
   in its own context (:mod:`blogwriter.render.mermaid`).
5. Content is plain text through Jinja2 autoescape, so documents contain no attacker markup.

The driver's environment: Playwright's Python package builds it from ``os.environ`` in
``playwright._impl.transport.get_driver_env``, with no public way to change it. :func:`start_driver`
replaces that function (once, idempotently) with one that returns the minimal environment held in a
context variable for the duration of the start, and the original behaviour otherwise. If a
Playwright upgrade removes the hook, rendering is switched off (:class:`RenderUnavailable`) rather
than started with the app's environment; ``tests/render/test_driver_env.py`` reads the live
driver's environment to prove the confinement.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import shutil
import sys
import tempfile
import time
import weakref
from collections.abc import AsyncGenerator, Callable, Mapping
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, cast
from urllib.parse import urlsplit

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    Route,
    async_playwright,
)
from playwright.async_api import Error as PlaywrightError

from blogwriter.render._logging import logger
from blogwriter.render.chromium import playwright_browsers_path
from blogwriter.render.errors import RenderError, RenderUnavailable
from blogwriter.render.settings import RenderSettings
from blogwriter.render.telemetry import StageTimer

__all__ = [
    "DEAD_END_PROXY",
    "FIT_CHECK_JS",
    "HOME_PREFIX",
    "BlockedRequest",
    "RenderLimiter",
    "RenderSession",
    "RequestGuard",
    "chromium_args",
    "default_limiter",
    "driver_env",
    "find_overflows",
    "install_driver_env_guard",
    "minimal_env",
    "start_driver",
    "sweep_stale_homes",
    "wait_for_fonts",
]

#: Proxy that nothing listens on. Playwright also forces loopback through it (``<-loopback>``),
#: so no request can reach the app's own port or any other local service.
DEAD_END_PROXY: Final = "http://127.0.0.1:9"
HOME_PREFIX: Final = "bw-render-"
_MINIMAL_PATH: Final = "/usr/local/bin:/usr/bin:/bin"
#: Bound for each teardown step: a wedged browser must not hold the job slot forever.
_TEARDOWN_TIMEOUT_S: Final = 10.0

_HARDENING_ARGS: Final = (
    "--host-resolver-rules=MAP * ~NOTFOUND",  # no name resolves: no DNS prefetch or exfiltration
    # WebRTC sends UDP (STUN) straight to IP literals: the route guard, the proxy, DNS and CSP
    # never see it. Forcing it through the (dead-end) proxy leaves it nothing to send from.
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

#: Variables Playwright's Python side adds for its driver; every other inherited one is dropped.
_DRIVER_PASSTHROUGH: Final = frozenset(
    {"PW_LANG_NAME", "PW_LANG_NAME_VERSION", "PW_CLI_DISPLAY_VERSION"}
)
_TRANSPORT_MODULE: Final = "playwright._impl.transport"
_GUARD_MARK: Final = "_blogwriter_confined_env"
_driver_env_var: ContextVar[Mapping[str, str] | None] = ContextVar(
    "blogwriter_render_driver_env", default=None
)


@dataclass(frozen=True, slots=True)
class BlockedRequest:
    url: str
    resource_type: str

    @property
    def scheme(self) -> str:
        return urlsplit(self.url).scheme


@dataclass  # no slots: Playwright tags handler objects with an attribute
class RequestGuard:
    """Aborts every routed request and records it. Installed on every context we create."""

    blocked: list[BlockedRequest] = field(default_factory=list[BlockedRequest])

    async def __call__(self, route: Route) -> None:
        request = route.request
        if request.url.startswith("data:"):  # pragma: no cover - Chromium never routes data:
            await route.continue_()
            return
        entry = BlockedRequest(url=request.url, resource_type=request.resource_type)
        self.blocked.append(entry)
        # Scheme and resource type only: a host name can carry text from the document.
        logger.warning(
            "render_request_blocked",
            extra={"scheme": entry.scheme, "resource_type": entry.resource_type},
        )
        await route.abort("blockedbyclient")


class RenderLimiter:
    """Job-level semaphore with in-flight telemetry. One per process (per event loop)."""

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


_LIMITERS: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, RenderLimiter] = (
    weakref.WeakKeyDictionary()
)


def default_limiter(settings: RenderSettings) -> RenderLimiter:
    """The process-wide limiter for the running event loop (created with the first caller's
    ``concurrency``)."""
    loop = asyncio.get_running_loop()
    limiter = _LIMITERS.get(loop)
    if limiter is None:
        limiter = _LIMITERS[loop] = RenderLimiter(settings.concurrency)
    return limiter


def sweep_stale_homes(max_age_s: float = 3600.0, root: Path | None = None) -> int:
    """Delete per-job homes left behind by a SIGKILL (crash-only: run at start-up, in a worker
    thread). Returns how many were removed."""
    base = root or Path(tempfile.gettempdir())
    removed = 0
    cutoff = time.time() - max_age_s
    for path in base.glob(f"{HOME_PREFIX}*"):
        try:
            if path.is_dir() and not path.is_symlink() and path.stat().st_mtime < cutoff:
                shutil.rmtree(path, ignore_errors=True)
                removed += 1
        except OSError:
            continue
    return removed


def minimal_env(home: Path) -> dict[str, str]:
    """The complete environment Chromium gets: nothing inherited from the app (no secrets)."""
    env = {
        "PATH": _MINIMAL_PATH,
        "HOME": str(home),
        "TMPDIR": str(home),
        "LANG": "C.UTF-8",
        "TZ": "UTC",
    }
    if sys.platform.startswith("linux"):
        env["XDG_CONFIG_HOME"] = str(home / ".config")
        env["XDG_CACHE_HOME"] = str(home / ".cache")
    return env


def driver_env(home: Path, browsers_path: Path) -> dict[str, str]:
    """The complete environment the Playwright driver gets: :func:`minimal_env` plus the browser
    registry it must search (it cannot derive it from the throwaway ``HOME``)."""
    return {**minimal_env(home), "PLAYWRIGHT_BROWSERS_PATH": str(browsers_path)}


def chromium_args(settings: RenderSettings) -> list[str]:
    js_flags = f"--max-old-space-size={settings.js_heap_limit_mb} --jitless"
    return [*_HARDENING_ARGS, f"--js-flags={js_flags}"]


def install_driver_env_guard() -> bool:
    """Route Playwright's driver environment through :data:`_driver_env_var` (idempotent).

    Returns ``False`` when the hook is gone (a Playwright upgrade changed its internals)."""
    try:
        transport: Any = importlib.import_module(_TRANSPORT_MODULE)
    except ImportError:  # pragma: no cover - depends on Playwright internals
        return False
    current: object = getattr(transport, "get_driver_env", None)
    if not callable(current):  # pragma: no cover - depends on Playwright internals
        return False
    if getattr(current, _GUARD_MARK, False):
        return True
    original = cast(Callable[[], dict[str, str]], current)

    def confined_driver_env() -> dict[str, str]:
        base = original()
        env = _driver_env_var.get()
        if env is None:
            return base
        return {**env, **{k: v for k, v in base.items() if k in _DRIVER_PASSTHROUGH}}

    setattr(confined_driver_env, _GUARD_MARK, True)
    transport.get_driver_env = confined_driver_env
    return True


async def start_driver(home: Path, browsers_path: Path) -> Playwright:
    """Start a Playwright driver whose environment is exactly :func:`driver_env`."""
    if not install_driver_env_guard():  # pragma: no cover - depends on Playwright internals
        raise RenderUnavailable(
            "the Playwright driver's environment cannot be confined (Playwright internals "
            "changed); rendering is off until blogwriter.render is updated"
        )
    token = _driver_env_var.set(driver_env(home, browsers_path))
    try:
        # The driver's start task copies this context, so it sees the minimal environment.
        return await async_playwright().start()
    finally:
        _driver_env_var.reset(token)


def _make_home() -> Path:
    return Path(tempfile.mkdtemp(prefix=HOME_PREFIX))


class RenderSession:
    """One render job: one Playwright driver and one Chromium launched for it, torn down with it.

    ``playwright`` reuses an already started driver (tests and benchmarks only); otherwise the
    session starts and stops its own (crash-only: nothing outlives the job). ``browsers_path``
    is the registry the driver searches (default: :func:`playwright_browsers_path`).
    """

    def __init__(
        self,
        settings: RenderSettings | None = None,
        *,
        playwright: Playwright | None = None,
        browsers_path: Path | None = None,
        timer: StageTimer | None = None,
    ) -> None:
        self.settings = settings if settings is not None else RenderSettings()
        self._external_pw = playwright
        self._browsers_path = browsers_path
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._home: Path | None = None
        self.guard = RequestGuard()
        self.timer = timer if timer is not None else StageTimer()

    @property
    def browser(self) -> Browser:
        if self._browser is None:
            raise RenderError("render session is not open")
        return self._browser

    @property
    def home(self) -> Path | None:
        """The per-job ``HOME``/``TMPDIR`` (``None`` once torn down)."""
        return self._home

    async def __aenter__(self) -> RenderSession:
        await self.open()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def open(self) -> None:
        """Start the driver (unless one was given) and launch Chromium. Cleans up on failure."""
        if self._home is not None:
            raise RenderError("render session is already open")
        self._home = await asyncio.to_thread(_make_home)
        try:
            with self.timer.stage("driver_start"):
                if self._external_pw is not None:
                    self._pw = self._external_pw
                else:
                    browsers = self._browsers_path
                    if browsers is None:
                        browsers = await asyncio.to_thread(playwright_browsers_path)
                    self._pw = await start_driver(self._home, browsers)
            with self.timer.stage("browser_launch"):
                self._browser = await self._launch()
        except BaseException:
            await self.close()
            raise

    async def _launch(self) -> Browser:
        if self._pw is None or self._home is None:
            raise RenderError("render session is not open")
        launch_env: dict[str, str | float | bool] = {**minimal_env(self._home)}
        try:
            return await self._pw.chromium.launch(
                headless=True,
                chromium_sandbox=self.settings.chromium_sandbox,
                env=launch_env,
                args=chromium_args(self.settings),
                proxy={"server": DEAD_END_PROXY},
                timeout=self.settings.action_timeout_ms,
            )
        except PlaywrightError as exc:
            message = str(exc).lower()
            if "sandbox" in message:
                raise RenderError(
                    "Chromium could not start its sandbox on this host (user namespaces "
                    "blocked?). Only set BW_RENDER_CHROMIUM_SANDBOX=0 after reviewing the "
                    "compensating controls."
                ) from exc
            if "executable doesn't exist" in message:
                raise RenderUnavailable("Chromium headless shell is not installed") from exc
            raise RenderError("Chromium failed to launch") from exc

    async def close(self) -> None:
        """Close Chromium, stop the driver and delete the per-job home (idempotent; each step is
        bounded so a wedged browser cannot hold the job slot)."""
        if self._browser is not None:
            browser, self._browser = self._browser, None
            with contextlib.suppress(Exception):
                async with asyncio.timeout(_TEARDOWN_TIMEOUT_S):
                    await browser.close()
        if self._pw is not None and self._external_pw is None:
            with contextlib.suppress(Exception):
                async with asyncio.timeout(_TEARDOWN_TIMEOUT_S):
                    await self._pw.stop()
        self._pw = None
        if self._home is not None:
            home, self._home = self._home, None
            await asyncio.to_thread(shutil.rmtree, home, ignore_errors=True)

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
    async def static_page(
        self, *, width: int, height: int, scale: float = 1.0
    ) -> AsyncGenerator[Page]:
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
    Playwright evaluates in a utility world the page's own scripts cannot touch)."""
    status: object = await page.evaluate("document.fonts.ready.then(() => document.fonts.status)")
    if status != "loaded":
        raise RenderError("fonts did not load")


FIT_CHECK_JS: Final = """() => {
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
    raw: object = await page.evaluate(FIT_CHECK_JS)
    out: list[str] = []
    for item in cast(list[dict[str, object]], raw):
        out.append(
            f"slide {item['slide']} {item['box']} (+{item['dy']}px tall, +{item['dx']}px wide)"
        )
    return out
