"""Job types of the publish package (contract W-B2, section 7).

``publish.article`` (input :class:`~blogwriter.domain.jobtypes.PublishInput`, id
``publish_job_id(articleId, version)``, re-runs ``-r<n>``; remote ids ``correlation``, ``run``):

==============  ==============================================================================
step            what it does
==============  ==============================================================================
``check``       the article is approved, at the job's version and names this job (else
                ``superseded``: a later run took over). **Holds** (approved -> in_review,
                ``lastError``, ``needs_attention`` alert, push) when the job starts more than
                ``PUBLISH_MAX_LATENESS`` after ``publishAt`` (``publish_late``), when the gates,
                clearance, metadata or brand no longer allow it, when the **current** blocklist
                hits what would ship (``blocklist_hit``), when the slug belongs to another
                document (``slug_taken``), and on the auto path unless autonomy is not paused, the
                autonomy decision allows it, the draft is not review-only and the veto notice was
                sent **for this approval** (at or after the article became approved) at least
                ``minVetoLeadHours`` ahead with a push **delivery receipt** and not vetoed.
                Without the portfolio settings it stops in a **waiting state** (alert and push,
                ``waiting_input`` key ``portfolio``; publish-now replaces it once configured).
                Then approved -> publishing (``lastOperation: publish``).
``render``      ``compose_document``, ``render_article``; diagrams (``render_mermaid_svg``), the
                1200x630 OG PNG, the hero (the owner's ``hero_image`` media, else the OG PNG) and
                the article's own images to Blob ``published/<slug>/`` with content-hashed names;
                builds and validates the document once (fail early)
``upsert``      the second render pass, the document validated again, then written to
                ``published-blogs`` (create when the slug is new to the article: a ``Conflict``
                is ``slug_taken``); slug ownership, ``live`` and pending correction notes (dated
                today) recorded on the article; redirect stubs for every previous slug
``dispatch``    record ``correlation`` first, dispatch the portfolio workflow, record
                ``dispatched``, find the run by its name, record ``run``
``wait_build``  poll the run until it concludes (``build_failed``, ``build_timeout``)
``verify``      egress GET ``<site>/blog/<slug>`` until the page carries the version marker of
                the upserted ``updatedAt`` (``has_published_marker``): a 200 with the old page is
                not enough (``verify_timeout``)
``finalize``    publishing -> published with the ``publish`` fields, ``entity.changed``, push
==============  ==============================================================================

``publish.unpublish`` (``ArticleJobInput``, random id): ``upsert`` (tombstone ``status:
unpublished``, ``live`` false), ``dispatch``, ``wait_build``, ``verify`` (the page is gone: 404
or 410), ``finalize`` (unpublished). ``publish.staging`` (``ArticleJobInput``): ``render``,
``upsert`` (``published-blogs-staging``; the same ``slug_taken`` check), ``dispatch`` (the
preview workflow; ``correlation`` only).

**Any failure** after ``check`` (a ``JobFailed``, or an error on the last attempt) moves the
article publishing/unpublishing -> publish_failed with ``lastError``, emits
``emit_alert("publish_failed", articleId)`` and pushes. The owner retries with publish-now or
unpublish (a fresh run), or retries the job from System: a run resumes at its failed step, moves
the article back, re-dispatches a build whose run failed and restarts a timed-out wait.

Steps are re-entrant: remote ids are recorded before awaiting the remote work and re-attached
on resume (a crash between the dispatch and the run lookup never dispatches twice); job inputs
and step outputs hold references, never content. Nothing here calls a model.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Final, Literal, cast

from pydantic import JsonValue, ValidationError

from blogwriter.articles import transition
from blogwriter.core.egress import EgressBlockedError, EgressError
from blogwriter.core.logging import register_log_fields
from blogwriter.db import Conflict, NotFound, Store, StoreError
from blogwriter.domain.articles import Article, Correction, has_published_marker
from blogwriter.domain.base import parse_iso, to_iso
from blogwriter.domain.common import Citation as SourceCitation
from blogwriter.domain.compose import (
    FrontmatterError,
    MetaIncomplete,
    compose_document,
    to_frontmatter,
)
from blogwriter.domain.confidentiality import (
    BlocklistNotConfigured,
    ConfidentialText,
    check_texts,
)
from blogwriter.domain.docstore import (
    InvalidStoredDocument,
    Stored,
    read_doc,
    read_singleton,
    update_doc,
)
from blogwriter.domain.jobflow import (
    JobDeferred,
    past_max_lateness,
    require_service,
    step_services,
)
from blogwriter.domain.jobtypes import (
    ENTITY_CHANGED,
    PUBLISH_ARTICLE,
    PUBLISH_MAX_LATENESS,
    PUBLISH_STAGING,
    PUBLISH_UNPUBLISH,
    ArticleJobInput,
    PublishInput,
    entity_changed,
)
from blogwriter.domain.ports import RenderFailed, ScheduleEntry, Services
from blogwriter.domain.research import Brief
from blogwriter.domain.settings import (
    AutonomySettings,
    Blocklist,
    BrandKit,
    CadenceSettings,
    EmployerPolicy,
    PillarSettings,
    auto_publish_decision,
)
from blogwriter.domain.states import ArticleStatus, TransitionError, TransitionRefused
from blogwriter.jobs import ClaimLost, JobFailed, JobRegistry, Step, StepContext
from blogwriter.notify import emit_alert
from blogwriter.publish.assets import (
    AssetError,
    PreparedAssets,
    hero_alt,
    prepare_assets,
    render_published,
)
from blogwriter.publish.contract import ContractUnavailableError
from blogwriter.publish.dialect import SourceTooLargeError
from blogwriter.publish.document import (
    PublishedImage,
    build_published_document,
    redirect_stub,
    tombstone,
)
from blogwriter.publish.portfolio import PortfolioClient, PortfolioError, correlation_id
from blogwriter.publish.published import (
    PUBLISHED_CONTAINER,
    STAGING_CONTAINER,
    ContractViolation,
    PublishedContainer,
    read_published,
    validate_document,
    write_published,
)
from blogwriter.publish.renderer import RenderedArticle, render_article
from blogwriter.publish.screen import article_texts, cleared_title, document_texts
from blogwriter.publish.settings import PublishSettings, publish_settings
from blogwriter.publish.state import dated_corrections, push_notice, studio_path

__all__ = [
    "FIND_RUN_GRACE",
    "FIND_RUN_TIMEOUT",
    "PORTFOLIO_PROMPT",
    "PUBLISH_STEPS",
    "STAGING_STEPS",
    "UNPUBLISH_STEPS",
    "PublishRuntime",
    "register_jobs",
    "schedule_entries",
]

S = ArticleStatus
PUBLISH_STEPS: Final = ("check", "render", "upsert", "dispatch", "wait_build", "verify", "finalize")
UNPUBLISH_STEPS: Final = ("upsert", "dispatch", "wait_build", "verify", "finalize")
STAGING_STEPS: Final = ("render", "upsert", "dispatch")
FIND_RUN_TIMEOUT: Final = timedelta(minutes=5)
"""How long ``dispatch`` looks for the run GitHub creates for a dispatch."""
FIND_RUN_GRACE: Final = timedelta(minutes=1)
"""After a crash between the dispatch and its receipt, look this long before dispatching again."""
FIND_RUN_POLL_SECONDS: Final = 5.0
MAX_PREVIOUS_SLUGS: Final = 20
PORTFOLIO_PROMPT: Final = "Publishing waits for the portfolio settings."
_GONE: Final = frozenset({404, 410})

type Operation = Literal["publish", "unpublish"]

_logger: Final = logging.getLogger("blogwriter.publish.jobs")
register_log_fields(
    "article_id", "job_id", "publish_code", "run_id", "assets", "operation", "polls"
)

_HOLD_DETAIL: Final[dict[str, str]] = {
    "publish_late": "The publish time passed while the app was down. Approve again.",
    "meta_incomplete": "The article details are incomplete.",
    "report_stale": "The gate report is for an older version.",
    "gates_blocking": "Some gates block publishing.",
    "clearance_required": "The clearance attestation is missing for this version.",
    "blocklist_hit": (
        "The article matches the blocklist (a term added after the gates ran). Run the gates "
        "again to see where."
    ),
    "blocklist_not_configured": "Save the blocklist once in Settings > Policy first.",
    "brand_incomplete": "Set the author name in the brand kit first.",
    "slug_taken": "Another published page already uses this slug.",
    "autonomy_paused": "Autonomy is paused.",
    "auto_publish_not_allowed": "Auto-publish is not allowed right now.",
    "review_only": "The draft is review-only.",
    "vetoed": "The auto-publish was vetoed.",
    "veto_notice_missing": "No veto notice was sent for this auto-publish.",
    "veto_not_delivered": "The veto notice has no delivery receipt.",
    "veto_notice_late": "The veto notice went out too late.",
    "portfolio_not_configured": "Publishing to the portfolio is not configured.",
}


@dataclass(frozen=True, slots=True)
class PublishRuntime:
    """What the steps use besides the services (tests replace these)."""

    settings: Callable[[], PublishSettings] = publish_settings
    portfolio: Callable[[PublishSettings], PortfolioClient] = field(
        default=PortfolioClient.from_settings
    )
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep


class _Superseded(Exception):
    """The article no longer names this job (raised inside a CAS mutation)."""


@dataclass(frozen=True, slots=True)
class _Env:
    services: Services
    settings: PublishSettings
    now: datetime

    @property
    def store(self) -> Store:
        return self.services.store


def register_jobs(registry: JobRegistry, *, runtime: PublishRuntime | None = None) -> None:
    """Register ``publish.article``, ``publish.unpublish`` and ``publish.staging``."""
    jobs = _PublishJobs(runtime or PublishRuntime())
    registry.register(
        PUBLISH_ARTICLE,
        [
            Step("check", jobs.check),
            Step("render", jobs.guarded("publish", jobs.render)),
            Step("upsert", jobs.guarded("publish", jobs.upsert)),
            Step("dispatch", jobs.guarded("publish", jobs.dispatch_publish)),
            Step("wait_build", jobs.guarded("publish", jobs.wait_build_publish)),
            Step("verify", jobs.guarded("publish", jobs.verify)),
            Step("finalize", jobs.guarded("publish", jobs.finalize)),
        ],
    )
    registry.register(
        PUBLISH_UNPUBLISH,
        [
            Step("upsert", jobs.guarded("unpublish", jobs.unpublish_upsert)),
            Step("dispatch", jobs.guarded("unpublish", jobs.dispatch_unpublish)),
            Step("wait_build", jobs.guarded("unpublish", jobs.wait_build_unpublish)),
            Step("verify", jobs.guarded("unpublish", jobs.unpublish_verify)),
            Step("finalize", jobs.guarded("unpublish", jobs.unpublish_finalize)),
        ],
    )
    registry.register(
        PUBLISH_STAGING,
        [
            Step("render", jobs.staging_render),
            Step("upsert", jobs.staging_upsert),
            Step("dispatch", jobs.staging_dispatch),
        ],
    )


def schedule_entries(cadence: CadenceSettings) -> list[ScheduleEntry]:
    """None: publishes are delayed jobs started by owner actions (contract section 8)."""
    return []


# --------------------------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------------------------


def _article_id(ctx: StepContext) -> str:
    try:
        return ArticleJobInput.from_json(ctx.input).article_id
    except (ValueError, ValidationError):
        raise JobFailed("invalid_input") from None


def _publish_input(ctx: StepContext) -> PublishInput:
    try:
        return PublishInput.from_json(ctx.input)
    except (ValueError, ValidationError):
        raise JobFailed("invalid_input") from None


def _output(ctx: StepContext, step: str) -> dict[str, JsonValue]:
    value = ctx.outputs.get(step)
    if not isinstance(value, dict):
        raise JobFailed("invalid_state", f"The {step} step left no result.")
    return value


def _checkpoint(ctx: StepContext) -> dict[str, JsonValue]:
    value = ctx.output
    return dict(value) if isinstance(value, dict) else {}


def _text(value: JsonValue, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise JobFailed("invalid_state", f"The step result has no {name}.")
    return value


async def _read_article(store: Store, article_id: str) -> Stored[Article]:
    try:
        return await read_doc(store, Article, article_id)
    except NotFound:
        raise JobFailed("article_missing") from None
    except InvalidStoredDocument:
        raise JobFailed("article_unreadable") from None


async def _compose(article: Article) -> str:
    try:
        return await asyncio.to_thread(compose_document, article.meta, article.markdown)
    except (MetaIncomplete, FrontmatterError):
        raise JobFailed("meta_incomplete") from None


async def _first_pass(document: str) -> RenderedArticle:
    try:
        return await asyncio.to_thread(render_article, document)
    except SourceTooLargeError:
        raise JobFailed("markdown_too_large") from None
    except FrontmatterError:
        raise JobFailed("meta_incomplete") from None
    except ContractUnavailableError:
        raise JobFailed("contract_unavailable") from None


def _render_failure(exc: RenderFailed) -> JobFailed:
    code = f"render_{exc.code}" if exc.code.isidentifier() else "render_failed"
    return JobFailed(code[:64], "A publish asset could not be rendered.")


def _portfolio_failure(exc: PortfolioError) -> Exception:
    """Transient GitHub trouble is retried by the runner; the rest fails the publish."""
    if exc.transient:
        return exc
    if exc.code == "not_configured":
        return JobFailed("portfolio_not_configured", _HOLD_DETAIL["portfolio_not_configured"])
    return JobFailed(f"portfolio_{exc.code}", "The portfolio build could not be started.")


def _previous_slugs(article: Article, slug: str) -> list[str]:
    old = [*article.publish.previous_slugs]
    if article.publish.slug is not None:
        old.append(article.publish.slug)
    return [item for item in dict.fromkeys(old) if item != slug]


def _updated_at(doc: dict[str, object]) -> str:
    value = doc.get("updatedAt")
    if not isinstance(value, str):
        raise JobFailed("invalid_state", "The document has no updatedAt.")
    return value


def _article_url(settings: PublishSettings, slug: str) -> str:
    try:
        return settings.article_url(slug)
    except ValueError:
        raise JobFailed(
            "portfolio_not_configured", _HOLD_DETAIL["portfolio_not_configured"]
        ) from None


def _compact(stamp: str) -> str:
    return "".join(char for char in stamp if char.isdigit())


def _blocklist_code(texts: dict[str, str], blocklist: Blocklist) -> str | None:
    """``blocklist_hit`` / ``blocklist_not_configured`` when ``texts`` may not leave the app."""
    try:
        check_texts(texts, blocklist)
    except BlocklistNotConfigured:
        return "blocklist_not_configured"
    except ConfidentialText:
        return "blocklist_hit"
    return None


# --------------------------------------------------------------------------------------------
# The job steps
# --------------------------------------------------------------------------------------------


class _PublishJobs:
    def __init__(self, runtime: PublishRuntime) -> None:
        self.runtime = runtime

    def _env(self) -> _Env:
        services = step_services()
        return _Env(services=services, settings=self.runtime.settings(), now=services.clock.now())

    def _now(self, env: _Env) -> datetime:
        return env.services.clock.now()

    async def _changed(self, ctx: StepContext, article_id: str) -> None:
        await ctx.emit(ENTITY_CHANGED, entity_changed("article", article_id))

    async def _title(self, env: _Env, article: Article) -> str | None:
        """The article title for a push, only when the current blocklist clears it."""
        try:
            blocklist, _ = await read_singleton(env.store, Blocklist, now=to_iso(env.now))
        except (StoreError, InvalidStoredDocument):
            return None
        return cleared_title(article.meta.title, blocklist)

    async def _push(
        self, env: _Env, prefix: str, article: Article, *, url: str | None = None
    ) -> None:
        await push_notice(
            env.services.notifier,
            prefix,
            await self._title(env, article),
            url=url or studio_path(article.id),
            tag=f"publish:{article.id}",
            article_id=article.id,
        )

    async def _screen(self, env: _Env, texts: Callable[[], dict[str, str]]) -> str | None:
        """The current blocklist over ``texts()`` (built in a thread): a hold or failure code,
        or ``None`` when everything may leave the app."""
        blocklist, _ = await read_singleton(env.store, Blocklist, now=to_iso(env.now))

        def check() -> str | None:
            return _blocklist_code(texts(), blocklist)

        return await asyncio.to_thread(check)

    async def _disclaimer(self, env: _Env) -> str | None:
        policy, _ = await read_singleton(env.store, EmployerPolicy, now=to_iso(env.now))
        return policy.disclaimer_long

    # ---------------------------------------------------------------------- failure handling

    def guarded(
        self, operation: Operation, step: Callable[[StepContext], Awaitable[JsonValue]]
    ) -> Callable[[StepContext], Awaitable[JsonValue]]:
        """Run ``step``; on a failure move the article to publish_failed and alert."""

        async def run(ctx: StepContext) -> JsonValue:
            try:
                return await step(ctx)
            except JobFailed as exc:
                if exc.code != "superseded":
                    await self._fail(ctx, operation, exc.code)
                raise
            except (ClaimLost, JobDeferred):
                raise
            except Exception as exc:
                if not await self._last_attempt(ctx):
                    raise
                code = f"{operation}_error"
                _logger.warning(
                    "publish_step_error",
                    extra={
                        "job_id": ctx.job_id,
                        "publish_code": code,
                        "error_type": type(exc).__name__,
                    },
                )
                await self._fail(ctx, operation, code)
                raise JobFailed(code, "Publishing hit an unexpected error.") from None

        return run

    async def _last_attempt(self, ctx: StepContext) -> bool:
        try:
            job = await step_services().jobs.get(ctx.job_id)
        except Exception:  # cannot tell: let the runner retry
            return False
        return ctx.attempt >= job.max_attempts

    async def _fail(self, ctx: StepContext, operation: Operation, code: str) -> None:
        """publishing/unpublishing -> publish_failed (if the article is still this job's),
        then the ``publish_failed`` alert and a push. Never hides the original failure."""
        try:
            article_id: str | None = _article_id(ctx)
        except JobFailed:
            article_id = None
        if article_id is None:
            emit_alert("publish_failed", None)
            return
        try:
            env = self._env()
            article = (await _read_article(env.store, article_id)).doc
        except (JobFailed, JobDeferred, StoreError):
            emit_alert("publish_failed", article_id)
            return
        active = S.PUBLISHING if operation == "publish" else S.UNPUBLISHING
        if article.status is active and article.publish.job_id == ctx.job_id:

            def update(doc: Article) -> Article:
                if doc.publish.job_id != ctx.job_id:
                    raise _Superseded()
                publish = doc.publish.model_copy(update={"last_error": code})
                return doc.model_copy(update={"publish": publish})

            try:
                await transition(
                    env.store,
                    article.id,
                    S.PUBLISH_FAILED,
                    actor="system",
                    now=self._now(env),
                    reason=code,
                    update=update,
                )
            except (_Superseded, TransitionError, TransitionRefused):
                pass
            except StoreError:
                _logger.error("publish_failure_not_recorded", extra={"article_id": article.id})
            else:
                await self._changed(ctx, article.id)
        emit_alert("publish_failed", article.id)
        prefix = "Publish failed: " if operation == "publish" else "Unpublish failed: "
        await self._push(env, prefix, article)
        _logger.warning(
            "publish_failed",
            extra={
                "article_id": article.id,
                "job_id": ctx.job_id,
                "publish_code": code,
                "operation": operation,
            },
        )

    async def _job_active(self, env: _Env, job_id: str) -> bool:
        try:
            job = await env.services.jobs.get(job_id)
        except NotFound:
            return False
        return job.is_active

    async def _stale_job(self, ctx: StepContext, env: _Env, article: Article) -> str | None:
        """The job id the article names when it is another job that has finished (a reference
        left by an earlier publish, the pipeline's auto path does not set it): not a rival."""
        named = article.publish.job_id
        if named is None or named == ctx.job_id:
            return None
        return None if await self._job_active(env, named) else named

    # ---------------------------------------------------------------------- article ownership

    async def _to_operation(
        self,
        ctx: StepContext,
        env: _Env,
        article_id: str,
        operation: Operation,
        *,
        version: int | None = None,
        stale: str | None = None,
    ) -> Stored[Article]:
        target = S.PUBLISHING if operation == "publish" else S.UNPUBLISHING

        def update(doc: Article) -> Article:
            if doc.publish.job_id not in (None, ctx.job_id, stale):
                raise _Superseded()
            if version is not None and doc.version != version:
                raise _Superseded()
            publish = doc.publish.model_copy(
                update={"last_operation": operation, "job_id": ctx.job_id, "last_error": None}
            )
            return doc.model_copy(update={"publish": publish, "last_error": None})

        try:
            stored = await transition(
                env.store, article_id, target, actor="system", now=self._now(env), update=update
            )
        except TransitionRefused as exc:
            raise JobFailed(exc.code) from None
        except (_Superseded, TransitionError):
            raise JobFailed("superseded", "A newer publish took over.") from None
        await self._changed(ctx, article_id)
        return stored

    async def _owned(self, ctx: StepContext, env: _Env, operation: Operation) -> Stored[Article]:
        """The article while this job owns it; a run retried from publish_failed takes it back."""
        stored = await _read_article(env.store, _article_id(ctx))
        article = stored.doc
        active = S.PUBLISHING if operation == "publish" else S.UNPUBLISHING
        ours = article.publish.job_id == ctx.job_id
        if article.status is active and ours:
            return stored
        retried = (
            article.status is S.PUBLISH_FAILED
            and article.publish.last_operation == operation
            and ours
        )
        if retried:
            return await self._to_operation(ctx, env, article.id, operation)
        raise JobFailed("superseded", "A newer publish took over.")

    async def _update_publish(
        self, ctx: StepContext, env: _Env, article_id: str, operation: Operation, **changes: object
    ) -> Stored[Article]:
        """Write publish fields while this job owns the article (no status change)."""
        active = S.PUBLISHING if operation == "publish" else S.UNPUBLISHING
        stamp = to_iso(self._now(env))
        corrections = changes.pop("corrections", None)

        def mutate(doc: Article) -> Article:
            if doc.status is not active or doc.publish.job_id != ctx.job_id:
                raise _Superseded()
            update: dict[str, object] = {
                "publish": doc.publish.model_copy(update=changes),
                "updated_at": stamp,
                "updated_by": "system",
            }
            if corrections is not None:
                update["corrections"] = corrections
            return doc.model_copy(update=update)

        try:
            return await update_doc(env.store, Article, article_id, mutate)
        except _Superseded:
            raise JobFailed("superseded", "A newer publish took over.") from None

    # ---------------------------------------------------------------------- publish.article

    async def check(self, ctx: StepContext) -> JsonValue:
        env = self._env()
        inp = _publish_input(ctx)
        stored = await _read_article(env.store, inp.article_id)
        article = stored.doc
        if article.status is S.PUBLISHING and article.publish.job_id == ctx.job_id:
            return {"articleId": article.id, "version": article.version}
        retry = (
            article.status is S.PUBLISH_FAILED
            and article.publish.last_operation == "publish"
            and article.publish.job_id == ctx.job_id
        )
        stale = await self._stale_job(ctx, env, article)
        current = article.status is S.APPROVED and article.publish.job_id in (
            None,
            ctx.job_id,
            stale,
        )
        if not (retry or current) or article.version != inp.version:
            raise JobFailed("superseded", "The article changed or a newer publish took over.")
        code = await self._hold_reason(env, article, inp)
        if code is not None:
            await self._hold(ctx, env, article, code)
            raise JobFailed(code, _HOLD_DETAIL.get(code))
        if not env.settings.portfolio_configured:
            await self._wait_for_portfolio(ctx, env, article, stale=stale)
            if not self.runtime.settings().portfolio_configured:
                await self._hold(ctx, env, article, "portfolio_not_configured")
                raise JobFailed(
                    "portfolio_not_configured", _HOLD_DETAIL["portfolio_not_configured"]
                )
        moved = await self._to_operation(
            ctx, env, article.id, "publish", version=inp.version, stale=stale
        )
        _logger.info("article_publishing", extra={"article_id": article.id, "job_id": ctx.job_id})
        return {"articleId": article.id, "version": moved.doc.version}

    async def _hold_reason(self, env: _Env, article: Article, inp: PublishInput) -> str | None:
        """Why this publish must not go ahead now (``None``: it may)."""
        stamp = to_iso(env.now)
        if past_max_lateness(inp.publish_at, env.now, PUBLISH_MAX_LATENESS):
            return "publish_late"
        meta = article.meta
        if meta.missing_for_publish():
            return "meta_incomplete"
        try:
            to_frontmatter(meta)
        except (MetaIncomplete, FrontmatterError):
            return "meta_incomplete"
        sha = article.compute_sha256()
        report = article.gates
        if report is None or report.content_sha256 != sha:
            return "report_stale"
        auto = inp.auto or article.publish.auto
        if (auto and not report.auto_publish_ready) or (not auto and report.blocking()):
            return "gates_blocking"
        if not article.clearance.valid_for(sha):
            return "clearance_required"
        disclaimer = await self._disclaimer(env)
        corrections = dated_corrections(article, env.now)
        blocked = await self._screen(
            env,
            lambda: article_texts(article, corrections=corrections, disclaimer=disclaimer),
        )
        if blocked is not None:
            return blocked
        brand, _ = await read_singleton(env.store, BrandKit, now=stamp)
        if not brand.complete:
            return "brand_incomplete"
        if auto:
            reason = await self._auto_reason(env, article, inp)
            if reason is not None:
                return reason
        slug = meta.slug
        if slug is None:
            return "meta_incomplete"
        existing = await read_published(env.store, PUBLISHED_CONTAINER, slug)
        if existing is not None and not article.owns_slug(slug):
            return "slug_taken"
        return None

    async def _auto_reason(self, env: _Env, article: Article, inp: PublishInput) -> str | None:
        """The auto path: not paused, allowed, not review-only, veto notice delivered in time."""
        stamp = to_iso(env.now)
        autonomy, _ = await read_singleton(env.store, AutonomySettings, now=stamp)
        if autonomy.paused:
            return "autonomy_paused"
        if not auto_publish_decision(autonomy, now=env.now).allowed:
            return "auto_publish_not_allowed"
        flags = article.calibration
        if article.review_only or (flags is not None and flags.review_only):
            return "review_only"
        veto = article.veto
        # Only a notice (or a veto) given for this approval counts: an approved article's
        # ``statusChangedAt`` is the approval, and a notice from an earlier approval of the same
        # article (pulled back, vetoed, re-run) says nothing about this content.
        approved_at = parse_iso(article.status_changed_at) if article.status is S.APPROVED else None

        def current(stamp: str | None) -> bool:
            return stamp is not None and (approved_at is None or parse_iso(stamp) >= approved_at)

        if veto is not None and current(veto.vetoed_at):
            return "vetoed"
        sent_at = veto.sent_at if veto is not None else None
        if veto is None or sent_at is None or not current(sent_at):
            return "veto_notice_missing"
        if veto.delivered is not True:
            return "veto_not_delivered"
        cadence, _ = await read_singleton(env.store, CadenceSettings, now=stamp)
        lead = timedelta(hours=cadence.min_veto_lead_hours)
        if parse_iso(sent_at) > parse_iso(inp.publish_at) - lead:
            return "veto_notice_late"
        return None

    async def _hold(self, ctx: StepContext, env: _Env, article: Article, code: str) -> None:
        """approved (or publish_failed) -> in_review with ``lastError``, alert and push."""

        def update(doc: Article) -> Article:
            publish = doc.publish.model_copy(update={"last_error": code})
            return doc.model_copy(update={"publish": publish, "last_error": code})

        try:
            await transition(
                env.store,
                article.id,
                S.IN_REVIEW,
                actor="system",
                now=self._now(env),
                reason=code,
                update=update,
            )
        except (TransitionError, TransitionRefused):
            return
        await self._changed(ctx, article.id)
        emit_alert("needs_attention", article.id)
        await self._push(env, "Publish held: ", article)
        _logger.warning(
            "publish_held",
            extra={"article_id": article.id, "job_id": ctx.job_id, "publish_code": code},
        )

    async def _wait_for_portfolio(
        self, ctx: StepContext, env: _Env, article: Article, *, stale: str | None
    ) -> None:
        """Stop in a visible waiting state until the portfolio is configured (never silently):
        the first time, record ``lastError``, alert and push; then ``waiting_input``."""
        if _checkpoint(ctx).get("waiting") != "portfolio":
            code = "portfolio_not_configured"
            stamp = to_iso(self._now(env))

            def mutate(doc: Article) -> Article:
                if doc.publish.job_id not in (None, ctx.job_id, stale):
                    raise _Superseded()
                publish = doc.publish.model_copy(update={"last_error": code, "job_id": ctx.job_id})
                return doc.model_copy(update={"publish": publish, "updated_at": stamp})

            try:
                await update_doc(env.store, Article, article.id, mutate)
            except _Superseded:
                raise JobFailed("superseded", "A newer publish took over.") from None
            await self._changed(ctx, article.id)
            emit_alert("needs_attention", article.id)
            await push_notice(
                env.services.notifier,
                "Publishing waits for portfolio settings: ",
                await self._title(env, article),
                url="/settings/integrations",
                tag=f"publish:{article.id}",
                article_id=article.id,
            )
            _logger.warning(
                "publish_waiting_for_portfolio",
                extra={"article_id": article.id, "job_id": ctx.job_id},
            )
            await ctx.checkpoint({"waiting": "portfolio"})
        missing = ", ".join(env.settings.missing_for_publish())
        await ctx.wait_for_input(f"{PORTFOLIO_PROMPT} Missing: {missing}.", key="portfolio")

    async def _prepare(self, env: _Env, article: Article) -> PreparedAssets:
        blobs = require_service(env.services.blobs, "storage")
        renderer = require_service(env.services.render, "render")
        document = await _compose(article)
        first = await _first_pass(document)
        stamp = to_iso(env.now)
        brand, _ = await read_singleton(env.store, BrandKit, now=stamp)
        pillars, _ = await read_singleton(env.store, PillarSettings, now=stamp)
        try:
            return await prepare_assets(
                article,
                first_pass=first,
                blobs=blobs,
                render=renderer,
                brand=brand,
                pillars=pillars,
            )
        except AssetError as exc:
            raise JobFailed(exc.code, "A publish asset could not be prepared.") from None
        except RenderFailed as exc:
            if exc.code == "timeout":
                raise
            raise _render_failure(exc) from None

    async def _sources(self, store: Store, article: Article) -> dict[str, SourceCitation]:
        if article.brief_id is None:
            return {}
        try:
            brief = (await read_doc(store, Brief, article.brief_id)).doc
        except (NotFound, InvalidStoredDocument):
            return {}
        return {citation.id: citation for citation in brief.citations}

    async def _build(
        self,
        env: _Env,
        article: Article,
        prepared: PreparedAssets,
        *,
        corrections: Sequence[Correction],
    ) -> dict[str, object]:
        """The validated published document (second render pass)."""
        site = env.settings.site_origin
        if site is None:
            raise JobFailed("portfolio_not_configured", _HOLD_DETAIL["portfolio_not_configured"])
        document = await _compose(article)
        try:
            rendered = await asyncio.to_thread(render_published, document, prepared)
        except AssetError as exc:
            raise JobFailed(exc.code, "The rendered assets no longer match the article.") from None
        except SourceTooLargeError:
            raise JobFailed("markdown_too_large") from None
        except ContractUnavailableError:
            raise JobFailed("contract_unavailable") from None
        hero = prepared.hero
        doc = build_published_document(
            article,
            rendered,
            prepared.assets,
            now=env.now,
            site_origin=site,
            hero=PublishedImage(hero.url, hero.width, hero.height, hero_alt(article, prepared)),
            sources=await self._sources(env.store, article),
            markdown=document,
            corrections=corrections,
            disclaimer=await self._disclaimer(env),
        )
        try:
            await validate_document(doc, now=env.now)
        except ContractViolation:
            raise JobFailed(
                "contract_invalid", "The published document breaks the contract."
            ) from None
        except ContractUnavailableError:
            raise JobFailed("contract_unavailable") from None
        blocked = await self._screen(env, lambda: document_texts(doc))
        if blocked is not None:
            raise JobFailed(blocked, _HOLD_DETAIL[blocked])
        return doc

    async def render(self, ctx: StepContext) -> JsonValue:
        env = self._env()
        article = (await self._owned(ctx, env, "publish")).doc
        prepared = await self._prepare(env, article)
        await self._build(env, article, prepared, corrections=dated_corrections(article, env.now))
        _logger.info(
            "publish_assets_ready",
            extra={"article_id": article.id, "job_id": ctx.job_id, "assets": len(prepared.assets)},
        )
        return prepared.to_json()

    def _prepared(self, ctx: StepContext, article: Article) -> PreparedAssets:
        try:
            prepared = PreparedAssets.from_json(ctx.outputs.get("render"))
        except (ValueError, ValidationError):
            raise JobFailed("invalid_state", "The render step left no assets.") from None
        if (
            prepared.content_sha256 != article.compute_sha256()
            or prepared.version != article.version
        ):
            raise JobFailed(
                "content_changed", "The article changed after its assets were rendered."
            )
        return prepared

    async def upsert(self, ctx: StepContext) -> JsonValue:
        env = self._env()
        article = (await self._owned(ctx, env, "publish")).doc
        prepared = self._prepared(ctx, article)
        slug = prepared.slug
        corrections = dated_corrections(article, env.now)
        doc = await self._build(env, article, prepared, corrections=corrections)
        previous = _previous_slugs(article, slug)
        if len(previous) > MAX_PREVIOUS_SLUGS:
            raise JobFailed("too_many_redirects", "This article was renamed too often.")
        await self._write_owned(ctx, env, article, slug, doc, PUBLISHED_CONTAINER)
        await self._update_publish(
            ctx,
            env,
            article.id,
            "publish",
            slug=slug,
            previous_slugs=previous,
            live=True,
            published_at=article.publish.published_at or to_iso(env.now),
            assets=list(prepared.assets),
            corrections=corrections,
        )
        for old in previous:
            stub = redirect_stub(old, slug, now=env.now)
            try:
                await write_published(
                    env.store, PUBLISHED_CONTAINER, stub, now=env.now, mode="upsert"
                )
            except ContractViolation:
                raise JobFailed(
                    "contract_invalid", "A redirect stub breaks the contract."
                ) from None
        await self._changed(ctx, article.id)
        return {
            "slug": slug,
            "updatedAt": _updated_at(doc),
            "previousSlugs": cast(JsonValue, previous),
        }

    async def _write_owned(
        self,
        ctx: StepContext,
        env: _Env,
        article: Article,
        slug: str,
        doc: dict[str, object],
        container: PublishedContainer,
    ) -> None:
        """Write the document under ``slug``: overwrite when the article owns it (or this step
        created it before a crash), else create it, so a slug that another document took in the
        meantime is ``slug_taken``, never overwritten."""
        created = _checkpoint(ctx).get("created") == slug
        if article.owns_slug(slug) or created:
            await self._write(env, container, doc, "upsert")
            return
        try:
            await self._write(env, container, doc, "create")
        except Conflict:
            existing = await read_published(env.store, container, slug)
            mine = existing is not None and existing.get("mdxContent") == doc.get("mdxContent")
            if not mine:
                raise JobFailed("slug_taken", _HOLD_DETAIL["slug_taken"]) from None
            await self._write(env, container, doc, "upsert")
        await ctx.checkpoint({"created": slug})

    async def _write(
        self,
        env: _Env,
        container: PublishedContainer,
        doc: dict[str, object],
        mode: Literal["create", "upsert"],
    ) -> None:
        try:
            await write_published(env.store, container, doc, now=env.now, mode=mode)
        except ContractViolation:
            raise JobFailed(
                "contract_invalid", "The published document breaks the contract."
            ) from None
        except ContractUnavailableError:
            raise JobFailed("contract_unavailable") from None

    # ---------------------------------------------------------------------- dispatch and build

    def _client(self, settings: PublishSettings) -> PortfolioClient:
        try:
            return self.runtime.portfolio(settings)
        except PortfolioError as exc:
            raise _portfolio_failure(exc) from None

    async def _find_run(
        self,
        env: _Env,
        client: PortfolioClient,
        workflow: str,
        correlation: str,
        *,
        within: timedelta,
    ) -> int | None:
        deadline = self._now(env) + within
        pause = min(env.settings.poll_seconds, FIND_RUN_POLL_SECONDS)
        while True:
            try:
                found = await client.find_run(workflow, correlation)
            except PortfolioError as exc:
                if not exc.transient:
                    raise _portfolio_failure(exc) from None
                found = None
            if found is not None:
                return found
            if self._now(env) >= deadline:
                return None
            await self.runtime.sleep(pause)

    async def _dispatch_run(
        self,
        ctx: StepContext,
        env: _Env,
        client: PortfolioClient,
        workflow: str,
        *,
        find: bool,
        suffix: str = "",
    ) -> int | None:
        """Dispatch once and (with ``find``) return the run: ``correlation`` is recorded
        before the dispatch, ``dispatched`` after it and ``run`` once found, so a resumed step
        re-attaches instead of dispatching again.

        A step resumed between the dispatch and its receipt (a crash, a lost answer) looks for
        the run first, for every workflow (staging previews too): GitHub may have it already.
        Without ``find`` the run found that way is recorded but not returned."""
        key_correlation, key_sent, key_run = (
            f"correlation{suffix}",
            f"dispatched{suffix}",
            f"run{suffix}",
        )
        remote = dict(ctx.remote_ids)
        if key_run in remote:
            return int(remote[key_run]) if find else None
        correlation = remote.get(key_correlation)
        resumed = correlation is not None
        if correlation is None:
            correlation = correlation_id(ctx.job_id, suffix)
            await ctx.record_remote_id(key_correlation, correlation)
        if key_sent not in remote:
            if resumed:
                found = await self._find_run(
                    env, client, workflow, correlation, within=FIND_RUN_GRACE
                )
                if found is not None:
                    await ctx.record_remote_id(key_run, str(found))
                    return found if find else None
            try:
                await client.dispatch(
                    workflow, ref=env.settings.portfolio_ref, correlation_id=correlation
                )
            except PortfolioError as exc:
                raise _portfolio_failure(exc) from None
            await ctx.record_remote_id(key_sent, "yes")
        if not find:
            return None
        found = await self._find_run(env, client, workflow, correlation, within=FIND_RUN_TIMEOUT)
        if found is None:
            raise JobFailed("run_not_found", "The portfolio build did not start.")
        await ctx.record_remote_id(key_run, str(found))
        _logger.info("portfolio_run_found", extra={"job_id": ctx.job_id, "run_id": found})
        return found

    async def _dispatch_step(self, ctx: StepContext, operation: Operation) -> JsonValue:
        env = self._env()
        article = (await self._owned(ctx, env, operation)).doc
        settings = env.settings
        workflow = settings.portfolio_workflow
        if not settings.portfolio_configured or workflow is None:
            raise JobFailed("portfolio_not_configured", _HOLD_DETAIL["portfolio_not_configured"])
        async with self._client(settings) as client:
            run_id = await self._dispatch_run(ctx, env, client, workflow, find=True)
        if run_id is None:
            raise JobFailed("run_not_found", "The portfolio build did not start.")
        await self._update_publish(ctx, env, article.id, operation, portfolio_run_id=str(run_id))
        return {"runId": run_id}

    async def dispatch_publish(self, ctx: StepContext) -> JsonValue:
        return await self._dispatch_step(ctx, "publish")

    async def dispatch_unpublish(self, ctx: StepContext) -> JsonValue:
        return await self._dispatch_step(ctx, "unpublish")

    async def _wait_build(self, ctx: StepContext, operation: Operation) -> JsonValue:
        env = self._env()
        article = (await self._owned(ctx, env, operation)).doc
        settings = env.settings
        workflow = settings.portfolio_workflow
        if not settings.portfolio_configured or workflow is None:
            raise JobFailed("portfolio_not_configured", _HOLD_DETAIL["portfolio_not_configured"])
        state: dict[str, JsonValue] = _checkpoint(ctx)
        dispatched = _output(ctx, "dispatch").get("runId")
        run_value = state.get("runId", dispatched)
        if not isinstance(run_value, int):
            raise JobFailed("invalid_state", "The dispatch step left no run.")
        run_id = run_value
        async with self._client(settings) as client:
            if state.get("failed"):
                # The job was retried after its build failed or timed out: build again.
                count = state.get("redispatches", 0)
                attempt = (count if isinstance(count, int) else 0) + 1
                found = await self._dispatch_run(
                    ctx, env, client, workflow, find=True, suffix=f"-{attempt}"
                )
                if found is None:
                    raise JobFailed("run_not_found", "The portfolio build did not start.")
                run_id = found
                state = {
                    "runId": run_id,
                    "startedAt": to_iso(self._now(env)),
                    "redispatches": attempt,
                }
                await ctx.checkpoint(state)
                await self._update_publish(
                    ctx, env, article.id, operation, portfolio_run_id=str(run_id)
                )
            elif "startedAt" not in state:
                state = {"runId": run_id, "startedAt": to_iso(self._now(env))}
                await ctx.checkpoint(state)
            started = _text(state.get("startedAt"), "startedAt")
            deadline = parse_iso(started) + timedelta(seconds=settings.build_timeout_seconds)
            while True:
                try:
                    status, conclusion = await client.run_status(run_id)
                except PortfolioError as exc:
                    if not exc.transient:
                        raise _portfolio_failure(exc) from None
                    status, conclusion = "unknown", None
                if status == "completed":
                    if conclusion == "success":
                        return {"runId": run_id, "conclusion": "success"}
                    await ctx.checkpoint({**state, "failed": "build_failed"})
                    raise JobFailed("build_failed", "The portfolio build failed.")
                if self._now(env) >= deadline:
                    await ctx.checkpoint({**state, "failed": "build_timeout"})
                    raise JobFailed("build_timeout", "The portfolio build took too long.")
                await self.runtime.sleep(settings.poll_seconds)

    async def wait_build_publish(self, ctx: StepContext) -> JsonValue:
        return await self._wait_build(ctx, "publish")

    async def wait_build_unpublish(self, ctx: StepContext) -> JsonValue:
        return await self._wait_build(ctx, "unpublish")

    async def _poll_page(
        self,
        ctx: StepContext,
        env: _Env,
        url: str,
        done: Callable[[int, bytes], bool],
    ) -> None:
        """GET ``url`` (cache-busted) until ``done(status, body)`` or the verify timeout."""
        state: dict[str, JsonValue] = _checkpoint(ctx)
        if state.get("failed") or "startedAt" not in state:
            state = {"startedAt": to_iso(self._now(env))}
            await ctx.checkpoint(state)
        started = _text(state.get("startedAt"), "startedAt")
        deadline = parse_iso(started) + timedelta(seconds=env.settings.verify_timeout_seconds)
        polls = 0
        while True:
            polls += 1
            probe = f"{url}?bw-verify={_compact(to_iso(self._now(env)))}"
            try:
                response = await env.services.egress.get(probe)
            except EgressBlockedError:
                raise JobFailed("verify_blocked", "The site address is not allowed.") from None
            except EgressError:
                response = None
            if response is not None and done(response.status, response.content):
                _logger.info("publish_verified", extra={"job_id": ctx.job_id, "polls": polls})
                return
            if self._now(env) >= deadline:
                await ctx.checkpoint({**state, "failed": "verify_timeout"})
                raise JobFailed("verify_timeout", "The site did not show the change in time.")
            await self.runtime.sleep(env.settings.poll_seconds)

    async def verify(self, ctx: StepContext) -> JsonValue:
        env = self._env()
        await self._owned(ctx, env, "publish")
        upserted = _output(ctx, "upsert")
        slug = _text(upserted.get("slug"), "slug")
        updated_at = _text(upserted.get("updatedAt"), "updatedAt")
        url = _article_url(env.settings, slug)

        def live(status: int, body: bytes) -> bool:
            return status == 200 and has_published_marker(body, updated_at)

        await self._poll_page(ctx, env, url, live)
        return {"url": url, "verifiedAt": to_iso(self._now(env))}

    async def finalize(self, ctx: StepContext) -> JsonValue:
        env = self._env()
        stored = await _read_article(env.store, _article_id(ctx))
        article = stored.doc
        verified = _output(ctx, "verify")
        url = _text(verified.get("url"), "url")
        slug = _text(_output(ctx, "upsert").get("slug"), "slug")
        if article.status is S.PUBLISHED and article.publish.job_id == ctx.job_id:
            return {"articleId": article.id, "slug": slug, "version": article.version}
        article = (await self._owned(ctx, env, "publish")).doc
        stamp = to_iso(self._now(env))

        def update(doc: Article) -> Article:
            if doc.publish.job_id != ctx.job_id:
                raise _Superseded()
            publish = doc.publish.model_copy(
                update={
                    "live": True,
                    "slug": slug,
                    "url": url,
                    "verified_at": stamp,
                    "last_published_at": stamp,
                    "last_published_version": doc.version,
                    "last_published_sha256": doc.compute_sha256(),
                    "published_at": doc.publish.published_at or stamp,
                    "last_error": None,
                }
            )
            return doc.model_copy(update={"publish": publish, "last_error": None})

        try:
            moved = await transition(
                env.store,
                article.id,
                S.PUBLISHED,
                actor="system",
                now=self._now(env),
                update=update,
            )
        except (_Superseded, TransitionError):
            raise JobFailed("superseded", "A newer publish took over.") from None
        await self._changed(ctx, article.id)
        await self._push(env, "Published: ", moved.doc)
        _logger.info(
            "article_published",
            extra={"article_id": article.id, "job_id": ctx.job_id, "operation": "publish"},
        )
        return {"articleId": article.id, "slug": slug, "version": moved.doc.version}

    # ---------------------------------------------------------------------- publish.unpublish

    async def unpublish_upsert(self, ctx: StepContext) -> JsonValue:
        env = self._env()
        article = (await self._owned(ctx, env, "unpublish")).doc
        slug = article.publish.slug
        if slug is None:
            raise JobFailed("not_published", "This article has no published page.")
        doc = tombstone(slug, now=env.now)
        await self._write(env, PUBLISHED_CONTAINER, doc, "upsert")
        await self._update_publish(ctx, env, article.id, "unpublish", live=False)
        await self._changed(ctx, article.id)
        return {"slug": slug, "updatedAt": _updated_at(doc)}

    async def unpublish_verify(self, ctx: StepContext) -> JsonValue:
        env = self._env()
        await self._owned(ctx, env, "unpublish")
        slug = _text(_output(ctx, "upsert").get("slug"), "slug")
        url = _article_url(env.settings, slug)

        def gone(status: int, body: bytes) -> bool:
            return status in _GONE

        await self._poll_page(ctx, env, url, gone)
        return {"url": url, "verifiedAt": to_iso(self._now(env))}

    async def unpublish_finalize(self, ctx: StepContext) -> JsonValue:
        env = self._env()
        stored = await _read_article(env.store, _article_id(ctx))
        article = stored.doc
        if article.status is S.UNPUBLISHED and article.publish.job_id == ctx.job_id:
            return {"articleId": article.id}
        article = (await self._owned(ctx, env, "unpublish")).doc
        stamp = to_iso(self._now(env))

        def update(doc: Article) -> Article:
            if doc.publish.job_id != ctx.job_id:
                raise _Superseded()
            publish = doc.publish.model_copy(
                update={"live": False, "url": None, "verified_at": stamp, "last_error": None}
            )
            return doc.model_copy(update={"publish": publish, "last_error": None})

        try:
            moved = await transition(
                env.store,
                article.id,
                S.UNPUBLISHED,
                actor="system",
                now=self._now(env),
                update=update,
            )
        except (_Superseded, TransitionError):
            raise JobFailed("superseded", "A newer operation took over.") from None
        await self._changed(ctx, article.id)
        await self._push(env, "Unpublished: ", moved.doc)
        _logger.info(
            "article_unpublished",
            extra={"article_id": article.id, "job_id": ctx.job_id, "operation": "unpublish"},
        )
        return {"articleId": article.id}

    # ---------------------------------------------------------------------- publish.staging

    async def _staging_article(self, env: _Env, ctx: StepContext) -> Article:
        article = (await _read_article(env.store, _article_id(ctx))).doc
        if article.purpose == "eval":
            raise JobFailed("eval_article", "Eval articles are never published.")
        return article

    async def staging_render(self, ctx: StepContext) -> JsonValue:
        env = self._env()
        article = await self._staging_article(env, ctx)
        prepared = await self._prepare(env, article)
        await self._build(env, article, prepared, corrections=dated_corrections(article, env.now))
        return prepared.to_json()

    async def staging_upsert(self, ctx: StepContext) -> JsonValue:
        env = self._env()
        article = await self._staging_article(env, ctx)
        prepared = self._prepared(ctx, article)
        slug = prepared.slug
        existing = await read_published(env.store, PUBLISHED_CONTAINER, slug)
        if existing is not None and not article.owns_slug(slug):
            raise JobFailed("slug_taken", _HOLD_DETAIL["slug_taken"])
        corrections = dated_corrections(article, env.now)
        doc = await self._build(env, article, prepared, corrections=corrections)
        await self._write(env, STAGING_CONTAINER, doc, "upsert")
        return {"slug": slug, "updatedAt": _updated_at(doc)}

    async def staging_dispatch(self, ctx: StepContext) -> JsonValue:
        env = self._env()
        settings = env.settings
        workflow = settings.preview_workflow
        if not settings.preview_configured or workflow is None:
            raise JobFailed("portfolio_not_configured", _HOLD_DETAIL["portfolio_not_configured"])
        async with self._client(settings) as client:
            await self._dispatch_run(ctx, env, client, workflow, find=False)
        correlation = ctx.remote_ids.get("correlation")
        return {"correlationId": correlation}
