"""HTTP routes of the publish package (contract W-B2). Owner-only; every non-GET passes CSRF
(core enforces both).

| Method | Path (``/api/articles/{id}``...) | Notes |
|---|---|---|
| POST | ``/approve`` | ``ApproveIn``, If-Match; in_review -> approved; ``publish.article`` |
| POST | ``/publish-now`` | ``PublishNowIn?``, If-Match; in_review, approved, publish_failed |
| POST | ``/unpublish`` | If-Match; 202 ``{jobId}`` |
| POST | ``/staging-preview`` | 202 ``{jobId}``; 503 ``portfolio_not_configured`` |
| GET | ``/publish-status`` | ``PublishStatusOut`` (no writes) |

Errors go through ``articles.article_problems()`` (store, transition and article errors, 412
with ``current``) plus the publish codes raised in :mod:`blogwriter.publish.approval`.
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, Response

from blogwriter.articles import article_problems
from blogwriter.domain.api import JobAccepted
from blogwriter.domain.deps import IfMatch, ServicesDep, versioned
from blogwriter.publish.approval import (
    approve,
    publish_status,
    start_staging,
    start_unpublish,
)
from blogwriter.publish.models import ApprovalOut, ApproveIn, PublishNowIn, PublishStatusOut
from blogwriter.publish.settings import PublishSettings, publish_settings

__all__ = ["get_router"]


def get_router(*, settings: Callable[[], PublishSettings] = publish_settings) -> APIRouter:
    """The publish routes. ``settings`` is for tests (default: the environment)."""
    router = APIRouter(prefix="/api/articles", tags=["publish"])

    @router.post("/{article_id}/approve")
    async def approve_article(
        article_id: str,
        body: ApproveIn,
        services: ServicesDep,
        if_match: IfMatch,
        response: Response,
    ) -> ApprovalOut:
        with article_problems():
            result = await approve(
                services, article_id, if_match=if_match, request=body, mode="approve"
            )
        response.headers["ETag"] = result.stored.etag
        return ApprovalOut(article=versioned(result.stored), job_id=result.job_id)

    @router.post("/{article_id}/publish-now")
    async def publish_now(
        article_id: str,
        services: ServicesDep,
        if_match: IfMatch,
        response: Response,
        body: PublishNowIn | None = None,
    ) -> ApprovalOut:
        request = ApproveIn(
            correction_note=body.correction_note if body is not None else None,
            slug=body.slug if body is not None else None,
        )
        with article_problems():
            result = await approve(
                services, article_id, if_match=if_match, request=request, mode="publish_now"
            )
        response.headers["ETag"] = result.stored.etag
        return ApprovalOut(article=versioned(result.stored), job_id=result.job_id)

    @router.post("/{article_id}/unpublish", status_code=202)
    async def unpublish(article_id: str, services: ServicesDep, if_match: IfMatch) -> JobAccepted:
        with article_problems():
            job_id = await start_unpublish(
                services, article_id, if_match=if_match, settings=settings()
            )
        return JobAccepted(job_id=job_id)

    @router.post("/{article_id}/staging-preview", status_code=202)
    async def staging_preview(article_id: str, services: ServicesDep) -> JobAccepted:
        with article_problems():
            job_id = await start_staging(services, article_id, settings=settings())
        return JobAccepted(job_id=job_id)

    @router.get("/{article_id}/publish-status")
    async def get_publish_status(article_id: str, services: ServicesDep) -> PublishStatusOut:
        with article_problems():
            return await publish_status(services, article_id)

    return router
