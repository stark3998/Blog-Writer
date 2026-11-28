"""The ``Embedder`` port (contract I-3): ``text-embedding-3-large`` at 1024 dimensions.

:class:`RouterEmbedder` serves :class:`blogwriter.domain.ports.Embedder` through
:meth:`blogwriter.ai.AIRouter.embed`, so every embeddings call gets what chat calls get: the
``Task.EMBED`` route and its sensitivity ceiling (``anonymized``), the spend brake for background
work (``BrakeEngaged`` passes through; job steps wrap calls in ``defer_on_brake``), the model's
limiter, retries on 429/5xx, and a priced :class:`~blogwriter.ai.UsageRecord` in the ledger.

Texts are sent in batches of :data:`DEFAULT_BATCH` (sequentially). A result that does not hold
one vector of :attr:`RouterEmbedder.dimensions` floats per text raises ``ValueError``: callers
fail closed (gate 4 reports the novelty check as not run), never compare partial vectors.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final, Protocol

from blogwriter.ai.ledger import Origin
from blogwriter.ai.router import EmbeddingResult

__all__ = ["DEFAULT_BATCH", "EMBEDDING_DIMENSIONS", "EmbeddingRouter", "RouterEmbedder"]

EMBEDDING_DIMENSIONS: Final = 1024
"""The plan's embedding size (``text-embedding-3-large`` shortened to 1024)."""
DEFAULT_BATCH: Final = 16


class EmbeddingRouter(Protocol):
    """What :class:`RouterEmbedder` uses of :class:`blogwriter.ai.AIRouter`."""

    async def embed(
        self,
        texts: Sequence[str],
        *,
        origin: Origin,
        job_id: str | None = None,
        dimensions: int | None = None,
    ) -> EmbeddingResult: ...


class RouterEmbedder:
    """:class:`~blogwriter.domain.ports.Embedder` over the AI router (see the module docs)."""

    def __init__(
        self,
        router: EmbeddingRouter,
        *,
        dimensions: int = EMBEDDING_DIMENSIONS,
        batch_size: int = DEFAULT_BATCH,
    ) -> None:
        if not 1 <= dimensions <= 3072:
            raise ValueError("dimensions must be between 1 and 3072")
        if batch_size < 1:
            raise ValueError("batch_size must be at least 1")
        self._router = router
        self._dimensions = dimensions
        self._batch = batch_size

    @property
    def dimensions(self) -> int:
        return self._dimensions

    async def embed(
        self, texts: Sequence[str], *, origin: Origin, job_id: str | None = None
    ) -> list[list[float]]:
        """One vector per text, in order (``[]`` for no texts)."""
        items = list(texts)
        vectors: list[list[float]] = []
        for start in range(0, len(items), self._batch):
            batch = items[start : start + self._batch]
            result = await self._router.embed(
                batch, origin=origin, job_id=job_id, dimensions=self._dimensions
            )
            got = result.vectors
            if len(got) != len(batch) or any(len(vector) != self._dimensions for vector in got):
                raise ValueError("the embeddings result does not match the request")
            vectors.extend(list(vector) for vector in got)
        return vectors
