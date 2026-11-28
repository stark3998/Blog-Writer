"""App wiring for rendering: ``startup(app)``, ``shutdown(app)`` and ``get_renderer``.

``startup`` builds a :class:`~blogwriter.render.service.Renderer` from ``BW_RENDER_*``, starts it
and installs it on ``app.state.render`` and as the process-wide default (the module-level render
functions use it). Start-up:

* fails (raises :class:`~blogwriter.render.errors.AssetIntegrityError`) when the bundled fonts or
  the pinned Mermaid bundle are missing or do not match their SHA-256: a broken image must not
  serve;
* only logs a ``render_unavailable`` warning when Chromium headless shell is not installed, so the
  rest of the app runs; every render call then raises
  :class:`~blogwriter.render.errors.RenderUnavailable`.

There is nothing long-lived to stop: every render job starts and stops its own driver and
browser. ``shutdown`` only uninstalls the renderer.
"""

from __future__ import annotations

from fastapi import FastAPI, Request

from blogwriter.render._logging import logger as log
from blogwriter.render.hardening import harden_process
from blogwriter.render.service import (
    Renderer,
    set_default_renderer,
    uninstall_default_renderer,
)
from blogwriter.render.settings import RenderSettings

__all__ = ["RenderLifecycle", "get_renderer", "shutdown", "startup"]


class RenderLifecycle:
    """The render lifecycle with explicit settings (or a prepared renderer, for tests)."""

    def __init__(
        self, settings: RenderSettings | None = None, *, renderer: Renderer | None = None
    ) -> None:
        self._settings = settings
        self._renderer = renderer

    async def startup(self, app: FastAPI) -> None:
        renderer = self._renderer if self._renderer is not None else Renderer(self._settings)
        if not renderer.settings.chromium_sandbox:
            # Compensating control for BW_RENDER_CHROMIUM_SANDBOX=0 (see render.hardening).
            # Raises on Linux when it cannot be applied: never run unsandboxed without it.
            applied = harden_process()
            log.warning(
                "render_sandbox_off",
                extra={"render_dumpable_hardened": applied},
            )
        await renderer.start()
        app.state.render = renderer
        set_default_renderer(renderer)

    async def shutdown(self, app: FastAPI) -> None:
        renderer: object = getattr(app.state, "render", None)
        if isinstance(renderer, Renderer):
            uninstall_default_renderer(renderer)
        app.state.render = None


async def startup(app: FastAPI) -> None:
    """Start rendering with settings from the environment (see :class:`RenderLifecycle`)."""
    await RenderLifecycle().startup(app)


async def shutdown(app: FastAPI) -> None:
    """Uninstall the renderer started by :func:`startup`."""
    await RenderLifecycle().shutdown(app)


def get_renderer(request: Request) -> Renderer:
    """FastAPI dependency: the app's renderer. Raises if ``startup`` has not run."""
    renderer: object = getattr(request.app.state, "render", None)
    if not isinstance(renderer, Renderer):
        raise RuntimeError("no renderer on app.state; call blogwriter.render.startup(app) first")
    return renderer
