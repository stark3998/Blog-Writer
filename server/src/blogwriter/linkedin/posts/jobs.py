"""The posts job types (contract section 7): derive, generate, check, render, stage, expire.

None of them is scheduled (``schedule_entries`` is empty): content jobs start from owner actions
(or the planner's plan approval, which is the owner's), and per-slot timing uses delayed jobs.
No step here, nor anything it calls, can reach the LinkedIn publisher.

==================  ==============================================  ============================
type                steps                                           input / id
==================  ==============================================  ============================
``posts.derive``    ``derive`` (DRAFT per post, sequential),        ``ArticleJobInput``,
                    ``check``                                       ``derive_job_id``
``posts.generate``  ``generate`` (DRAFT; REVISE with an             :class:`GenerateInput`,
                    instruction), ``check``                         random
``posts.check``     ``check``                                       ``PostJobInput``, random
``posts.render``    ``render``, ``save``                            ``PostJobInput``, random
``posts.stage``     ``check``, ``render``, ``upload``, ``token``,   ``SlotJobInput``,
                    ``ready``                                       ``stage_job_id``, slot-60
``posts.expire``    ``expire``                                      ``PostJobInput``,
                                                                    ``expire_job_id``, expiresAt
==================  ==============================================  ============================
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Final, Literal

from pydantic import Field, JsonValue

from blogwriter.db import Conflict, Filter, NotFound, OrderBy, Store
from blogwriter.domain.articles import Article
from blogwriter.domain.base import DocId, to_iso
from blogwriter.domain.common import MediaRef
from blogwriter.domain.docstore import (
    InvalidStoredDocument,
    create_doc,
    find_docs,
    read_doc,
    read_singleton,
    update_doc,
)
from blogwriter.domain.jobflow import require_service, step_services
from blogwriter.domain.jobtypes import (
    ENTITY_CHANGED,
    POSTS_CHECK,
    POSTS_DERIVE,
    POSTS_EXPIRE,
    POSTS_GENERATE,
    POSTS_RENDER,
    POSTS_STAGE,
    ArticleJobInput,
    PostJobInput,
    SlotJobInput,
    entity_changed,
)
from blogwriter.domain.planner import WeekPlan
from blogwriter.domain.ports import ScheduleEntry, Services
from blogwriter.domain.posts import Post
from blogwriter.domain.settings import (
    Blocklist,
    BrandKit,
    CadenceSettings,
    EmployerPolicy,
    LaneSettings,
)
from blogwriter.domain.states import POST_MACHINE, PostStatus
from blogwriter.jobs import JobFailed, JobRegistry, Step, StepContext
from blogwriter.jobs.models import DEFAULT_MAX_ATTEMPTS
from blogwriter.linkedin.posts._logging import logger
from blogwriter.linkedin.posts.carousel import (
    RenderRefused,
    render_post_media,
    same_media,
    spec_sha256,
    with_alt_texts,
)
from blogwriter.linkedin.posts.checks import (
    DERIVED_CLAIMS_CHECK,
    check_post,
    claims_content_sha256,
    lane_findings,
    lint_content_sha256,
    lint_state,
    load_check_settings,
    near_duplicate_content_sha256,
)
from blogwriter.linkedin.posts.derive import (
    DERIVABLE_STATUSES,
    GenerationFailed,
    default_go_live_mode,
    draft_post,
    load_generation_context,
    mark_generation_failed,
    save_draft,
)
from blogwriter.linkedin.posts.staging import (
    LinkedInTransport,
    StagingFailed,
    StagingTransport,
    cancel_slot_jobs,
    current_for_stage,
    expire_post,
    stage_check,
    stage_media,
    stage_ready,
    stage_screen,
    stage_token,
    stage_upload,
    staging_failed,
)

__all__ = [
    "MAX_DERIVATIVE_ORDINAL",
    "GenerateInput",
    "check_and_save",
    "ensure_derivative",
    "plan_derivative_ids",
    "register_jobs",
    "save_checks",
    "save_media",
    "schedule_entries",
]

MAX_DERIVATIVE_ORDINAL: Final = 50
_CLOSED: Final = frozenset({PostStatus.ARCHIVED, PostStatus.DELETED, PostStatus.EXPIRED})


class GenerateInput(PostJobInput):
    """``posts.generate``: a ``PostJobInput`` plus what to (re)generate. Planner-created posts
    may carry their plan and slot (``Post.id_for_plan_slot``)."""

    what: Literal["all", "hooks", "text", "carousel"] = "all"
    instruction: str | None = Field(default=None, min_length=1, max_length=1000)
    """The owner's regenerate instruction (owner text, checked against the blocklist)."""
    week_plan_id: DocId | None = None
    slot_id: str | None = Field(default=None, max_length=40)


# --------------------------------------------------------------------------------------------
# Shared step helpers
# --------------------------------------------------------------------------------------------


async def _read_post(store: Store, post_id: str) -> Post | None:
    try:
        return (await read_doc(store, Post, post_id)).doc
    except NotFound:
        return None


async def _changed(ctx: StepContext, post_id: str) -> None:
    await ctx.emit(ENTITY_CHANGED, entity_changed("post", post_id))


def _json_ids(ids: Sequence[str]) -> list[JsonValue]:
    return list(ids)


def _ids(output: JsonValue, key: str = "postIds") -> list[str] | None:
    if not isinstance(output, dict):
        return None
    value = output.get(key)
    if not isinstance(value, list):
        return None
    return [item for item in value if isinstance(item, str)]


async def save_checks(store: Store, post_id: str, checked: Post, *, now: datetime) -> bool:
    """Write the check results that are still about the post's current content (an edit
    meanwhile makes them stale: those are dropped). ``True`` when something was written."""
    stamp = to_iso(now)
    record = checked.served_by[-1] if checked.served_by else None

    def mutate(current: Post) -> Post | None:
        update: dict[str, object] = {}
        if checked.lint is not None and checked.lint.content_sha256 == lint_content_sha256(current):
            update["lint"] = checked.lint
        claims = checked.derived_claims
        if claims is not None and claims.content_sha256 == claims_content_sha256(current):
            update["derived_claims"] = claims
            if (
                record is not None
                and record.step == DERIVED_CLAIMS_CHECK
                and record.at == claims.ran_at
                and record not in current.served_by
            ):
                update["served_by"] = [*current.served_by, record][-50:]
        near = checked.near_duplicate
        if near is not None and near.content_sha256 == near_duplicate_content_sha256(current):
            update["near_duplicate"] = near
        if not update:
            return None
        update["updated_at"] = stamp
        return current.model_copy(update=update)

    try:
        await update_doc(store, Post, post_id, mutate)
    except NotFound:
        return False
    return True


async def check_and_save(
    services: Services, post_id: str, *, origin: Literal["owner", "background"], job_id: str
) -> bool:
    """Run every check on the post and save the current results (skips generating posts)."""
    post = await _read_post(services.store, post_id)
    if post is None or post.status is PostStatus.GENERATING:
        return False
    checked = await check_post(services, post, origin=origin, job_id=job_id)
    return await save_checks(services.store, post_id, checked, now=services.clock.now())


# --------------------------------------------------------------------------------------------
# posts.derive
# --------------------------------------------------------------------------------------------


async def plan_derivative_ids(store: Store, article: Article, *, cap: int) -> list[str]:
    """The derivative post ids this run creates: up to ``cap`` minus the article's active
    derivatives, at ordinals whose id is still free (an archived derivative keeps its id)."""
    try:
        existing = await find_docs(
            store,
            Post,
            [Filter("articleId", "==", article.id), Filter("lane", "==", "derivative")],
            strict=True,
        )
    except InvalidStoredDocument:
        raise JobFailed("posts_unreadable", "Some posts could not be read.") from None
    active = [stored for stored in existing if stored.doc.status not in _CLOSED]
    taken = {stored.doc.id for stored in existing}
    want = max(0, cap - len(active))
    planned: list[str] = []
    for ordinal in range(1, MAX_DERIVATIVE_ORDINAL + 1):
        if len(planned) >= want:
            break
        post_id = Post.id_for_derivative(article.id, article.version, ordinal)
        if post_id not in taken:
            planned.append(post_id)
    return planned


async def ensure_derivative(
    store: Store,
    post_id: str,
    article: Article,
    *,
    pillar: str,
    mode: Literal["one_click", "kit"],
    now: datetime,
) -> Post:
    """The derivative post ``post_id``: created ``generating`` (``Conflict``: it exists)."""
    stamp = to_iso(now)
    post = Post(
        id=post_id,
        created_at=stamp,
        updated_at=stamp,
        status=PostStatus.GENERATING,
        status_changed_at=stamp,
        lane="derivative",
        pillar=pillar,
        format="text",
        article_id=article.id,
        brief_id=article.brief_id,
        idea_id=article.idea_id,
        week_plan_id=article.week_plan_id,
        go_live_mode=mode,
    )
    try:
        return (await create_doc(store, post)).doc
    except Conflict:
        return (await read_doc(store, Post, post_id)).doc


async def _derive(ctx: StepContext) -> JsonValue:
    job_input = ArticleJobInput.from_json(ctx.input)
    services = step_services()
    store = services.store
    now = services.clock.now()
    try:
        article = (await read_doc(store, Article, job_input.article_id)).doc
    except NotFound:
        raise JobFailed("article_missing", "The article no longer exists.") from None
    if article.status not in DERIVABLE_STATUSES:
        raise JobFailed(
            "article_not_ready", "Posts are derived from an article in review or approved."
        )
    pillar = article.meta.pillar
    if pillar is None:
        raise JobFailed("meta_incomplete", "The article has no pillar.")
    planned = _ids(ctx.output)
    if planned is None:
        lanes, _ = await read_singleton(store, LaneSettings, now=to_iso(now))
        planned = await plan_derivative_ids(store, article, cap=lanes.derivatives_max_per_week)
        await ctx.checkpoint({"postIds": _json_ids(planned)})
    generation = await load_generation_context(store, now=now)
    mode = await default_go_live_mode(store, now=now)
    siblings: list[Post] = []
    drafted = 0
    for post_id in planned:
        post = await ensure_derivative(store, post_id, article, pillar=pillar, mode=mode, now=now)
        if post.status is not PostStatus.GENERATING:
            siblings.append(post)
            continue
        try:
            changes = await draft_post(
                services,
                post,
                generation,
                origin=job_input.origin,
                job_id=ctx.job_id,
                siblings=siblings,
            )
        except GenerationFailed as exc:
            await mark_generation_failed(store, post_id, exc, now=now)
            await _changed(ctx, post_id)
            if exc.fatal:
                raise JobFailed(exc.code, exc.detail) from None
            continue
        saved = await save_draft(store, post_id, changes, now=now)
        if saved is not None:
            siblings.append(saved)
            drafted += 1
        await _changed(ctx, post_id)
    logger.info(
        "posts_derived",
        extra={"article_id": article.id, "posts_count": drafted, "job_id": ctx.job_id},
    )
    return {"postIds": _json_ids(planned), "drafted": drafted}


async def _check_derived(ctx: StepContext) -> JsonValue:
    job_input = ArticleJobInput.from_json(ctx.input)
    services = step_services()
    planned = _ids(ctx.outputs.get("derive")) or []
    checked = 0
    for post_id in planned:
        if await check_and_save(services, post_id, origin=job_input.origin, job_id=ctx.job_id):
            checked += 1
            await _changed(ctx, post_id)
    return {"postIds": _json_ids(planned), "checked": checked}


# --------------------------------------------------------------------------------------------
# posts.generate
# --------------------------------------------------------------------------------------------


async def _post_from_plan(
    services: Services, job_input: GenerateInput, *, now: datetime
) -> Post | None:
    """Create the post of a planned slot (planner enqueues ``posts.generate`` with
    ``Post.id_for_plan_slot``; only this package creates posts)."""
    store = services.store
    plans: list[WeekPlan] = []
    if job_input.week_plan_id is not None:
        try:
            plans.append((await read_doc(store, WeekPlan, job_input.week_plan_id)).doc)
        except NotFound:
            return None
    else:
        found = await find_docs(
            store,
            WeekPlan,
            [Filter("status", "==", "approved")],
            order_by=OrderBy("weekStart", descending=True),
            limit=8,
        )
        plans.extend(stored.doc for stored in found)
    for plan in plans:
        for planned in plan.slots:
            expected = Post.id_for_plan_slot(plan.id, planned.id)
            if job_input.post_id not in (planned.post_id, expected):
                continue
            flagship = plan.flagship
            pillar = planned.pillar or (flagship.pillar if flagship is not None else None)
            if pillar is None:
                return None
            lane = planned.lane or "native"
            stamp = to_iso(now)
            post = Post(
                id=job_input.post_id,
                created_at=stamp,
                updated_at=stamp,
                status=PostStatus.GENERATING,
                status_changed_at=stamp,
                lane=lane,
                pillar=pillar,
                format=planned.format or "text",
                slot=planned.slot,
                week_plan_id=plan.id,
                article_id=flagship.article_id if lane == "derivative" and flagship else None,
                go_live_mode=await default_go_live_mode(store, now=now),
            )
            try:
                return (await create_doc(store, post)).doc
            except Conflict:
                return await _read_post(store, job_input.post_id)
    return None


async def _siblings(store: Store, post: Post) -> list[Post]:
    if post.lane != "derivative" or post.article_id is None:
        return []
    found = await find_docs(
        store,
        Post,
        [Filter("articleId", "==", post.article_id), Filter("lane", "==", "derivative")],
    )
    return [
        s.doc
        for s in found
        if s.doc.id != post.id and s.doc.status not in _CLOSED and s.doc.display_text
    ]


async def _generate(ctx: StepContext) -> JsonValue:
    job_input = GenerateInput.from_json(ctx.input)
    services = step_services()
    store = services.store
    now = services.clock.now()
    post = await _read_post(store, job_input.post_id)
    if post is None:
        post = await _post_from_plan(services, job_input, now=now)
    if post is None:
        raise JobFailed("post_missing", "The post no longer exists.")
    if post.status is not PostStatus.GENERATING:
        return {"postId": post.id, "skipped": post.status.value}
    generation = await load_generation_context(store, now=now)
    fixed = None if (job_input.what == "all" and post.lane == "derivative") else post.format
    try:
        changes = await draft_post(
            services,
            post,
            generation,
            origin=job_input.origin,
            job_id=ctx.job_id,
            what=job_input.what,
            instruction=job_input.instruction,
            fixed_format=fixed,
            siblings=await _siblings(store, post),
        )
    except GenerationFailed as exc:
        await mark_generation_failed(store, post.id, exc, now=now)
        await _changed(ctx, post.id)
        raise JobFailed(exc.code, exc.detail) from None
    saved = await save_draft(store, post.id, changes, now=now)
    await _changed(ctx, post.id)
    return {"postId": post.id, "drafted": saved is not None}


async def _check_generated(ctx: StepContext) -> JsonValue:
    job_input = GenerateInput.from_json(ctx.input)
    services = step_services()
    checked = await check_and_save(
        services, job_input.post_id, origin=job_input.origin, job_id=ctx.job_id
    )
    if checked:
        await _changed(ctx, job_input.post_id)
    return {"postId": job_input.post_id, "checked": checked}


# --------------------------------------------------------------------------------------------
# posts.check
# --------------------------------------------------------------------------------------------


async def _check(ctx: StepContext) -> JsonValue:
    job_input = PostJobInput.from_json(ctx.input)
    services = step_services()
    post = await _read_post(services.store, job_input.post_id)
    if post is None:
        raise JobFailed("post_missing", "The post no longer exists.")
    checked = await check_and_save(
        services, job_input.post_id, origin=job_input.origin, job_id=ctx.job_id
    )
    if checked:
        await _changed(ctx, job_input.post_id)
    return {"postId": job_input.post_id, "checked": checked}


# --------------------------------------------------------------------------------------------
# posts.render
# --------------------------------------------------------------------------------------------

_MEDIA_EDITABLE: Final = frozenset(
    {PostStatus.DRAFT, PostStatus.APPROVED, PostStatus.STAGED, PostStatus.NEEDS_ATTENTION}
)


async def _render(ctx: StepContext) -> JsonValue:
    job_input = PostJobInput.from_json(ctx.input)
    services = step_services()
    store = services.store
    now = services.clock.now()
    post = await _read_post(store, job_input.post_id)
    if post is None:
        raise JobFailed("post_missing", "The post no longer exists.")
    render = require_service(services.render, "render")
    blobs = require_service(services.blobs, "storage")
    stamp = to_iso(now)
    brand, _ = await read_singleton(store, BrandKit, now=stamp)
    blocklist, _ = await read_singleton(store, Blocklist, now=stamp)
    policy, _ = await read_singleton(store, EmployerPolicy, now=stamp)
    try:
        rendered = await render_post_media(
            post,
            render=render,
            blobs=blobs,
            brand=brand,
            blocklist=blocklist,
            disclaimer=policy.disclaimer_short,
        )
    except RenderRefused as exc:
        logger.warning("posts_render_refused", extra={"post_id": post.id, "error_code": exc.code})
        raise JobFailed(exc.code, exc.detail) from None
    # Job outputs hold references only: the alt texts (slide text) are derived again from the
    # same slides when the media are saved.
    media: list[JsonValue] = [
        item.model_dump(mode="json", by_alias=True, exclude={"alt_text"}) for item in rendered.media
    ]
    return {"postId": post.id, "specSha256": rendered.spec_sha256, "media": media}


async def save_media(
    services: Services, post_id: str, media: Sequence[MediaRef], spec_sha: str, *, now: datetime
) -> Post | None:
    """Store rendered media on the post if its slides are still the rendered ones. New media on
    an approved or staged post invalidate the approval (back to draft). Lint re-runs (alt
    texts). ``None`` when nothing was written."""
    store = services.store
    stamp = to_iso(now)
    settings = await load_check_settings(store, now=now)
    current_post = await _read_post(store, post_id)
    if current_post is None or spec_sha256(current_post.carousel_spec) != spec_sha:
        return None
    media = with_alt_texts(media, current_post)
    lane = await lane_findings(store, current_post, lanes=settings.lanes, now=now)
    previous: list[Post] = []

    def mutate(current: Post) -> Post | None:
        if spec_sha256(current.carousel_spec) != spec_sha:
            return None
        if current.status not in _MEDIA_EDITABLE:
            return None
        if current.status is PostStatus.NEEDS_ATTENTION and current.intent is not None:
            return None
        if [m.name for m in current.media] == [m.name for m in media] and same_media(
            current.media, media
        ):
            return None
        update: dict[str, object] = {"media": list(media), "updated_at": stamp}
        if current.status in (PostStatus.APPROVED, PostStatus.STAGED):
            update.update(
                status=POST_MACHINE.transition(current.status, PostStatus.DRAFT),
                status_changed_at=stamp,
                approval=None,
                staging=None,
            )
        changed = current.model_copy(update=update)
        previous[:] = [current]
        lint = lint_state(changed, settings, slop=services.slop, now=now, extra=lane)
        return changed.model_copy(update={"lint": lint})

    try:
        stored = await update_doc(store, Post, post_id, mutate)
    except NotFound:
        return None
    if not previous:
        return None
    before = previous[0]
    if before.status in (PostStatus.APPROVED, PostStatus.STAGED) and before.slot is not None:
        await cancel_slot_jobs(services.jobs, post_id, before.slot.utc)
    return stored.doc


async def _save_render(ctx: StepContext) -> JsonValue:
    job_input = PostJobInput.from_json(ctx.input)
    services = step_services()
    output = ctx.outputs.get("render")
    if not isinstance(output, dict):
        raise JobFailed("render_missing", "The render step left no output.")
    raw_media = output.get("media")
    spec_sha = output.get("specSha256")
    if not isinstance(raw_media, list) or not isinstance(spec_sha, str):
        raise JobFailed("render_missing", "The render step left no output.")
    media = [MediaRef.model_validate(item) for item in raw_media]
    saved = await save_media(services, job_input.post_id, media, spec_sha, now=services.clock.now())
    if saved is not None:
        await _changed(ctx, job_input.post_id)
    return {"postId": job_input.post_id, "saved": saved is not None}


# --------------------------------------------------------------------------------------------
# posts.stage
# --------------------------------------------------------------------------------------------


def _stage_steps(transport: StagingTransport) -> list[Step]:
    async def go_on(ctx: StepContext) -> tuple[Services, SlotJobInput, Post | None, str | None]:
        job_input = SlotJobInput.from_json(ctx.input)
        services = step_services()
        first = ctx.outputs.get("check")
        sha = first.get("approvalSha256") if isinstance(first, dict) else None
        if not isinstance(sha, str):
            return services, job_input, None, None
        return services, job_input, await current_for_stage(services, job_input, sha), sha

    async def fail(
        ctx: StepContext, services: Services, job_input: SlotJobInput, exc: StagingFailed
    ) -> JobFailed:
        await staging_failed(services, job_input, exc, job_id=ctx.job_id, now=services.clock.now())
        await _changed(ctx, job_input.post_id)
        return JobFailed(exc.code, exc.detail)

    async def check(ctx: StepContext) -> JsonValue:
        job_input = SlotJobInput.from_json(ctx.input)
        services = step_services()
        return await stage_check(services, job_input, now=services.clock.now())

    async def render(ctx: StepContext) -> JsonValue:
        services, job_input, post, _ = await go_on(ctx)
        if post is None:
            return {"skipped": True}
        try:
            await stage_screen(services, post, now=services.clock.now())
            await stage_media(services, post, now=services.clock.now())
        except StagingFailed as exc:
            raise await fail(ctx, services, job_input, exc) from None
        return {"ok": True}

    async def upload(ctx: StepContext) -> JsonValue:
        services, job_input, post, _ = await go_on(ctx)
        if post is None:
            return {"skipped": True}
        try:
            uploaded = await stage_upload(services, post, transport)
        except StagingFailed as exc:
            raise await fail(ctx, services, job_input, exc) from None
        except Exception:
            if ctx.attempt < DEFAULT_MAX_ATTEMPTS:
                raise  # transient: the runner retries (the slot is an hour away)
            failure = StagingFailed("upload_failed", "LinkedIn did not take the media.")
            raise await fail(ctx, services, job_input, failure) from None
        return {"uploaded": uploaded}

    async def token(ctx: StepContext) -> JsonValue:
        services, job_input, post, _ = await go_on(ctx)
        if post is None:
            return {"skipped": True}
        try:
            await stage_token(services, transport, now=services.clock.now())
        except StagingFailed as exc:
            raise await fail(ctx, services, job_input, exc) from None
        except Exception:
            if ctx.attempt < DEFAULT_MAX_ATTEMPTS:
                raise
            failure = StagingFailed(
                "token_check_failed", "The LinkedIn token could not be checked."
            )
            raise await fail(ctx, services, job_input, failure) from None
        return {"ok": True}

    async def ready(ctx: StepContext) -> JsonValue:
        services, job_input, post, sha = await go_on(ctx)
        if post is None or sha is None:
            return {"skipped": True}
        staged = await stage_ready(
            services, job_input, sha, job_id=ctx.job_id, now=services.clock.now()
        )
        if staged is not None:
            await _changed(ctx, job_input.post_id)
        return {"postId": job_input.post_id, "staged": staged is not None}

    return [
        Step("check", check),
        Step("render", render),
        Step("upload", upload),
        Step("token", token),
        Step("ready", ready),
    ]


# --------------------------------------------------------------------------------------------
# posts.expire
# --------------------------------------------------------------------------------------------


async def _expire(ctx: StepContext) -> JsonValue:
    job_input = PostJobInput.from_json(ctx.input)
    services = step_services()
    outcome = await expire_post(services.store, job_input.post_id, now=services.clock.now())
    if outcome == "expired":
        await _changed(ctx, job_input.post_id)
    return {"postId": job_input.post_id, "outcome": outcome}


# --------------------------------------------------------------------------------------------
# Wiring
# --------------------------------------------------------------------------------------------


def register_jobs(registry: JobRegistry, *, transport: StagingTransport | None = None) -> None:
    """Register every posts job type (``transport``: the LinkedIn transport staging uses;
    default :class:`~blogwriter.linkedin.posts.staging.LinkedInTransport`)."""
    staging_transport = transport if transport is not None else LinkedInTransport()
    registry.register(POSTS_DERIVE, [Step("derive", _derive), Step("check", _check_derived)])
    registry.register(
        POSTS_GENERATE, [Step("generate", _generate), Step("check", _check_generated)]
    )
    registry.register(POSTS_CHECK, [Step("check", _check)])
    registry.register(POSTS_RENDER, [Step("render", _render), Step("save", _save_render)])
    registry.register(POSTS_STAGE, _stage_steps(staging_transport))
    registry.register(POSTS_EXPIRE, [Step("expire", _expire)])


def schedule_entries(cadence: CadenceSettings) -> list[ScheduleEntry]:
    """None: posts run from owner actions and delayed jobs only (contract section 8)."""
    del cadence
    return []
