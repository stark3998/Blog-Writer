"""HTTP routes of the AI illustrations (every route is owner-only and every non-GET passes CSRF,
both enforced by core; every route takes ``If-Match`` with the article's etag).

| Method | Path (``/api/articles/{id}``...) | Notes |
|---|---|---|
| POST | ``/images`` | ``{count: 1-3, direction?}``; 202 ``{article, jobId}`` |
| POST | ``/images/{illustrationId}/hero`` | the candidate becomes the hero image |
| DELETE | ``/hero`` | back to the typographic card |
| POST | ``/images/{illustrationId}/insert`` | ``{heading}``: an image after it (new version) |
| PUT | ``/images/{illustrationId}/alt`` | ``{altText}``: the candidate, hero and body image |
| DELETE | ``/images/{illustrationId}`` | discard an unused candidate |

Starting a run needs: the article in review (in_review or needs_attention; planned answers 409
``draft_missing``, other states 409 ``read_only_state``), a title (409 ``meta_incomplete``),
illustrations switched on (``ModelPreferences.imagesEnabled``, 409 ``images_disabled``), an image
route (503 ``images_unavailable``), the blocklist saved and clearing the article's public
material and the direction (409 ``blocklist_not_configured``, 422 ``confidential_text``) and no
run in progress (409 ``images_in_progress`` with ``jobId``). The run's id is written on the
article (``illustrationJobId``) before it is enqueued. Owner actions on candidates answer 404
for an unknown candidate, 409 ``read_only_state`` outside owner states, 409
``illustration_in_use`` / ``illustration_inserted`` / ``too_many_media``, 422
``heading_not_found`` / ``invalid_alt_text``.
"""

from __future__ import annotations

import logging
import secrets
from collections.abc import Generator
from contextlib import contextmanager
from typing import Final

from fastapi import APIRouter, Response

from blogwriter.articles import (
    HeadingNotFound,
    IllustrationInserted,
    IllustrationInUse,
    InvalidAltText,
    MediaFull,
    StaleArticle,
    article_problems,
    choose_hero,
    clear_hero,
    discard_illustration,
    edit_illustration_alt,
    insert_illustration,
    link_illustration_job,
)
from blogwriter.articles.models import ArticleSaved
from blogwriter.core.errors import ProblemError
from blogwriter.core.logging import register_log_fields
from blogwriter.db import NotFound, StoreError
from blogwriter.domain.api import Versioned
from blogwriter.domain.articles import Article
from blogwriter.domain.base import to_iso
from blogwriter.domain.compose import MetaIncomplete
from blogwriter.domain.confidentiality import check_texts
from blogwriter.domain.deps import ClockDep, IfMatch, ServicesDep, StoreDep, versioned
from blogwriter.domain.docstore import read_doc, read_singleton
from blogwriter.domain.jobtypes import (
    ILLUSTRATIONS_GENERATE,
    IllustrationsInput,
    illustrations_job_id,
)
from blogwriter.domain.ports import JobQueue, Services, ServiceUnavailable, image_generator
from blogwriter.domain.settings import Blocklist, ModelPreferences
from blogwriter.domain.states import ArticleStatus, WriterConflict
from blogwriter.illustrations.briefs import load_subject
from blogwriter.illustrations.models import AltIn, IllustrationsAccepted, IllustrationsIn, InsertIn

__all__ = ["REVIEW_STATES", "get_router", "illustration_problems", "start_illustrations"]

REVIEW_STATES: Final = frozenset({ArticleStatus.IN_REVIEW, ArticleStatus.NEEDS_ATTENTION})
"""The owner-editable review states a run may start from."""
_ACTIVE: Final = frozenset({"queued", "running", "waiting_input"})

_logger: Final = logging.getLogger("blogwriter.illustrations.routes")
register_log_fields("article_id", "job_id")


@contextmanager
def illustration_problems() -> Generator[None]:
    """``articles.article_problems()`` plus the illustration errors."""
    with article_problems():
        try:
            yield
        except HeadingNotFound:
            raise ProblemError(
                422,
                code=HeadingNotFound.code,
                detail="No section heading has that text. Pick one from the draft.",
            ) from None
        except InvalidAltText:
            raise ProblemError(
                422, code=InvalidAltText.code, detail="Describe the image in a few words."
            ) from None
        except IllustrationInUse:
            raise ProblemError(
                409,
                code=IllustrationInUse.code,
                detail="It is the hero image or in the draft. Clear the hero or remove it first.",
            ) from None
        except IllustrationInserted:
            raise ProblemError(
                409, code=IllustrationInserted.code, detail="The draft already shows this image."
            ) from None
        except MediaFull:
            raise ProblemError(
                409, code=MediaFull.code, detail="This article holds too many images already."
            ) from None


def _images_unavailable() -> ProblemError:
    return ProblemError(
        503,
        code="images_unavailable",
        detail="Illustrations need an image model, and none is configured.",
        headers={"Retry-After": "5"},
    )


async def _active_job(jobs: JobQueue, job_id: str | None) -> str | None:
    if job_id is None:
        return None
    try:
        job = await jobs.get(job_id)
    except NotFound:
        return None
    return job.id if job.status in _ACTIVE else None


async def start_illustrations(
    services: Services, article_id: str, body: IllustrationsIn, *, if_match: str
) -> tuple[Article, str, str]:
    """Check every gate, name the run on the article (If-Match), then enqueue it. Returns the
    article, its new etag and the job id."""
    store = services.store
    now = services.clock.now()
    stamp = to_iso(now)
    current = await read_doc(store, Article, article_id)
    if current.etag != if_match:
        raise StaleArticle(current)
    article = current.doc
    if article.status is ArticleStatus.PLANNED:
        raise ProblemError(409, code="draft_missing", detail="Draft the article first.")
    if article.status not in REVIEW_STATES:
        raise WriterConflict(article.status, "owner")
    if article.meta.title is None:
        raise MetaIncomplete(["title"])
    preferences, _ = await read_singleton(store, ModelPreferences, now=stamp)
    if not preferences.images_enabled:
        raise ProblemError(
            409,
            code="images_disabled",
            detail="Switch illustrations on in Settings > Models first.",
        )
    if image_generator(services.ai) is None:
        raise _images_unavailable()
    if services.blobs is None:
        raise ServiceUnavailable("storage")
    blocklist, _ = await read_singleton(store, Blocklist, now=stamp)
    subject = await load_subject(store, article, direction=body.direction, now=stamp)
    check_texts(subject.texts(), blocklist)
    running = await _active_job(services.jobs, article.illustration_job_id)
    if running is not None:
        raise ProblemError(
            409,
            code="images_in_progress",
            detail="Illustrations are being made for this article. Wait for them.",
            extensions={"jobId": running},
        )
    job_id = illustrations_job_id(article.id, secrets.token_hex(6))
    stored = await link_illustration_job(store, article.id, job_id, if_match=if_match, now=now)
    payload = IllustrationsInput(
        article_id=article.id,
        count=body.count,
        direction=body.direction.strip() if body.direction else None,
        origin="owner",
    )
    try:
        await services.jobs.enqueue(ILLUSTRATIONS_GENERATE, payload.to_json(), job_id=job_id)
    except ValueError:
        raise _images_unavailable() from None
    except (StoreError, TimeoutError):
        _logger.warning("illustrations_enqueue_failed", extra={"article_id": article.id})
        raise ProblemError(503, code="jobs_unavailable", headers={"Retry-After": "5"}) from None
    _logger.info("illustrations_started", extra={"article_id": article.id, "job_id": job_id})
    return stored.doc, stored.etag, job_id


def get_router() -> APIRouter:
    """The illustration routes (``/api/articles/{id}/images``... and ``/hero``)."""
    router = APIRouter(prefix="/api/articles", tags=["illustrations"])

    @router.post("/{article_id}/images", status_code=202)
    async def post_images(
        article_id: str,
        body: IllustrationsIn,
        services: ServicesDep,
        if_match: IfMatch,
        response: Response,
    ) -> IllustrationsAccepted:
        with illustration_problems():
            article, etag, job_id = await start_illustrations(
                services, article_id, body, if_match=if_match
            )
        response.headers["ETag"] = etag
        return IllustrationsAccepted(
            article=Versioned[Article](item=article, etag=etag), job_id=job_id
        )

    @router.post("/{article_id}/images/{illustration_id}/hero")
    async def post_hero(
        article_id: str,
        illustration_id: str,
        store: StoreDep,
        clock: ClockDep,
        if_match: IfMatch,
        response: Response,
    ) -> Versioned[Article]:
        with illustration_problems():
            stored = await choose_hero(
                store, article_id, illustration_id, if_match=if_match, now=clock.now()
            )
        return versioned(stored, response)

    @router.delete("/{article_id}/hero")
    async def delete_hero(
        article_id: str, store: StoreDep, clock: ClockDep, if_match: IfMatch, response: Response
    ) -> Versioned[Article]:
        with illustration_problems():
            stored = await clear_hero(store, article_id, if_match=if_match, now=clock.now())
        return versioned(stored, response)

    @router.post("/{article_id}/images/{illustration_id}/insert")
    async def post_insert(
        article_id: str,
        illustration_id: str,
        body: InsertIn,
        store: StoreDep,
        clock: ClockDep,
        if_match: IfMatch,
        response: Response,
    ) -> ArticleSaved:
        with illustration_problems():
            saved = await insert_illustration(
                store,
                article_id,
                illustration_id,
                heading=body.heading,
                if_match=if_match,
                now=clock.now(),
            )
        response.headers["ETag"] = saved.stored.etag
        return ArticleSaved(item=saved.stored.doc, etag=saved.stored.etag, findings=saved.findings)

    @router.put("/{article_id}/images/{illustration_id}/alt")
    async def put_alt(
        article_id: str,
        illustration_id: str,
        body: AltIn,
        store: StoreDep,
        clock: ClockDep,
        if_match: IfMatch,
        response: Response,
    ) -> ArticleSaved:
        with illustration_problems():
            blocklist, _ = await read_singleton(store, Blocklist, now=to_iso(clock.now()))
            check_texts({"altText": body.alt_text}, blocklist)
            saved = await edit_illustration_alt(
                store,
                article_id,
                illustration_id,
                body.alt_text,
                if_match=if_match,
                now=clock.now(),
            )
        response.headers["ETag"] = saved.stored.etag
        return ArticleSaved(item=saved.stored.doc, etag=saved.stored.etag, findings=saved.findings)

    @router.delete("/{article_id}/images/{illustration_id}")
    async def delete_image(
        article_id: str,
        illustration_id: str,
        store: StoreDep,
        clock: ClockDep,
        if_match: IfMatch,
        response: Response,
    ) -> Versioned[Article]:
        with illustration_problems():
            stored = await discard_illustration(
                store, article_id, illustration_id, if_match=if_match, now=clock.now()
            )
        return versioned(stored, response)

    return router
