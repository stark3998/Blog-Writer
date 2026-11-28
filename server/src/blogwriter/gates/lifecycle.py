"""App wiring of the gates: ``app.state.slop`` (the ``SlopChecker`` port for voice, posts and
engagement, and the detectors gate 4 runs)."""

from __future__ import annotations

from fastapi import FastAPI

from blogwriter.gates.slop import SlopDetectors

__all__ = ["GatesLifecycle", "shutdown", "startup"]


class GatesLifecycle:
    """``startup``: ``app.state.slop = SlopDetectors()``; ``shutdown`` removes it."""

    def __init__(self, detectors: SlopDetectors | None = None) -> None:
        self._detectors = detectors

    async def startup(self, app: FastAPI) -> None:
        app.state.slop = self._detectors if self._detectors is not None else SlopDetectors()

    async def shutdown(self, app: FastAPI) -> None:
        app.state.slop = None


async def startup(app: FastAPI) -> None:
    await GatesLifecycle().startup(app)


async def shutdown(app: FastAPI) -> None:
    await GatesLifecycle().shutdown(app)
