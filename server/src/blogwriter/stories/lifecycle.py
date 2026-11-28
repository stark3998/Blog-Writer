"""The stories lifecycle: puts the confidentiality classifier on ``app.state.classifier``.

Start it before ``ServicesLifecycle`` and the jobs runner (contract I-1, step 4). The classifier
resolves the services on every call (``StateServices(app)``), so the order among the other
service lifecycles does not matter.
"""

from __future__ import annotations

from fastapi import FastAPI

from blogwriter.domain.ports import StateServices
from blogwriter.stories.classify import StoriesClassifier

__all__ = ["StoriesLifecycle"]


class StoriesLifecycle:
    """``startup``: ``app.state.classifier = StoriesClassifier(lambda: StateServices(app))``."""

    async def startup(self, app: FastAPI) -> None:
        app.state.classifier = StoriesClassifier(lambda: StateServices(app))

    async def shutdown(self, app: FastAPI) -> None:
        app.state.classifier = None
