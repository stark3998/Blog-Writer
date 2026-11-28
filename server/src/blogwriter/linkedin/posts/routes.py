"""HTTP routes of linkedin posts (contract W-B3; section 6 lists them).

Every route is owner-only and every non-GET passed CSRF (core). Mutations of an existing post
take ``If-Match``; a stale etag is 412 with ``current``. Any edit of an approved or staged post
(text, title, hashtags, format, pillar, attributions, link exception, slides) moves it back to
``draft``: the approval is invalidated and its staging jobs are cancelled. ``generating``,
``publishing``, ``posted``, ``kit`` and the other states the transport or a job owns are
read-only here (409 ``read_only_state``).

Nothing here publishes: ``POST /api/posts/{id}/publish`` belongs to the transport.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Annotated, Final, Literal

from fastapi import APIRouter, Body, Query, Response
from fastapi.responses import HTMLResponse
from pydantic import JsonValue, ValidationError

from blogwriter.core.errors import ProblemError
from blogwriter.db import Filter, NotFound, OrderBy, Store
from blogwriter.domain.api import (
    PREVIEW_HEADERS,
    JobAccepted,
    Page,
    Versioned,
    clamp_limit,
    decode_cursor,
    encode_cursor,
)
from blogwriter.domain.articles import Article
from blogwriter.domain.base import is_timestamp, parse_iso, to_iso
from blogwriter.domain.confidentiality import (
    BlocklistNotConfigured,
    ConfidentialText,
    check_texts,
    require_ai_use,
)
from blogwriter.domain.deps import (
    AIDep,
    IfMatch,
    ServicesDep,
    StoreDep,
    etag_mismatch,
    problems,
    versioned,
)
from blogwriter.domain.docstore import (
    Stored,
    create_doc,
    find_docs,
    read_doc,
    read_singleton,
    replace_doc,
    update_doc,
)
from blogwriter.domain.golive import (
    PlanSlotRefused,
    link_plan_slot,
    plan_slot_for,
    taken_slots,
)
from blogwriter.domain.jobflow import enqueue_once
from blogwriter.domain.jobtypes import (
    POSTS_CHECK,
    POSTS_DERIVE,
    POSTS_EXPIRE,
    POSTS_GENERATE,
    POSTS_RENDER,
    ArticleJobInput,
    PostJobInput,
    derive_job_id,
    expire_job_id,
)
from blogwriter.domain.planner import PlannedSlot, WeekPlan
from blogwriter.domain.ports import RenderFailed, Services
from blogwriter.domain.posts import (
    Approval,
    Attribution,
    LinkException,
    Post,
    Slot,
    into_kit,
)
from blogwriter.domain.research import QuickSearch
from blogwriter.domain.settings import (
    BrandKit,
    CadenceSettings,
    EmployerPolicy,
    LaneSettings,
    PillarSettings,
)
from blogwriter.domain.signals import Idea
from blogwriter.domain.slots import conflicting_slots
from blogwriter.domain.states import (
    IDEA_MACHINE,
    POST_MACHINE,
    IdeaStatus,
    PostStatus,
    TransitionError,
)
from blogwriter.domain.stories import PovEntry, StoryEntry
from blogwriter.domain.weeks import local_date_time, slot_utc
from blogwriter.linkedin.lint import fold_index
from blogwriter.linkedin.posts._logging import logger
from blogwriter.linkedin.posts._post import approved_subset, first_line, with_extra
from blogwriter.linkedin.posts.carousel import (
    MEDIA_FORMATS,
    InvalidSpec,
    media_missing,
    parse_spec,
    spec_json,
    spec_with_title,
)
from blogwriter.linkedin.posts.checks import (
    failing_checks,
    forbidden_topic_post_findings,
    lint_post_text,
    load_check_settings,
    near_duplicate_state,
    store_lint_state,
)
from blogwriter.linkedin.posts.derive import (
    APPROVED_ARTICLE_STATUSES,
    DERIVABLE_STATUSES,
    default_go_live_mode,
)
from blogwriter.linkedin.posts.jobs import GenerateInput
from blogwriter.linkedin.posts.models import (
    ApproveIn,
    CarouselIn,
    LintIn,
    LintOut,
    PostCreated,
    PostCreateIn,
    PostEditIn,
    PostRow,
    PostSummary,
    RegenerateIn,
)
from blogwriter.linkedin.posts.serialize import (
    LittleTextError,
    payload_matches,
    payload_units,
    serialize,
)
from blogwriter.linkedin.posts.staging import cancel_slot_jobs, enqueue_slot_jobs
from blogwriter.stories import load_bank, local_findings

__all__ = ["EDITABLE", "get_router"]

EDITABLE: Final = frozenset(
    {
        PostStatus.DRAFT,
        PostStatus.APPROVED,
        PostStatus.STAGED,
        PostStatus.EXPIRED,
        PostStatus.NEEDS_ATTENTION,
    }
)
"""States the owner edits in (``needs_attention`` only before any publish attempt)."""
_DEFAULT_LIMIT: Final = 20
_NEWEST: Final = OrderBy("updatedAt", descending=True)
_WRITERS: Final[Mapping[PostStatus, str]] = {
    PostStatus.GENERATING: "posts",
    PostStatus.PUBLISHING: "linkedin",
    PostStatus.POSTED: "linkedin",
    PostStatus.KIT: "linkedin",
    PostStatus.NEEDS_ATTENTION: "linkedin",
    PostStatus.MISSED: "planner",
}


# --------------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------------


def _read_only(post: Post) -> ProblemError:
    """409 ``read_only_state`` with the post status as ``state`` (``status`` is the problem's
    reserved HTTP status member) and who writes the post now, like the articles routes."""
    writer = _WRITERS.get(post.status, "none")
    return ProblemError(
        409,
        code="read_only_state",
        detail=f"Read-only while {post.status.value}.",
        extensions={"state": post.status.value, "writer": writer},
    )


def _editable(post: Post) -> None:
    if post.status not in EDITABLE or (
        post.status is PostStatus.NEEDS_ATTENTION and post.intent is not None
    ):
        raise _read_only(post)


def _to_draft(post: Post, stamp: str) -> dict[str, object]:
    """The fields that move an edited post back to ``draft`` (approval invalidated)."""
    if post.status is PostStatus.DRAFT:
        return {"updated_at": stamp}
    target = POST_MACHINE.transition(post.status, PostStatus.DRAFT)
    return {
        "status": target,
        "status_changed_at": stamp,
        "updated_at": stamp,
        "approval": None,
        "staging": None,
    }


async def _current(store: Store, post_id: str, if_match: str) -> Stored[Post]:
    current = await read_doc(store, Post, post_id)
    if current.etag != if_match:
        raise etag_mismatch(current)
    return current


async def _after_unapproval(services: Services, before: Post) -> None:
    if before.status in (PostStatus.APPROVED, PostStatus.STAGED) and before.slot is not None:
        await cancel_slot_jobs(services.jobs, before.id, before.slot.utc)


def _problem(status: int, code: str, detail: str, **extensions: JsonValue) -> ProblemError:
    return ProblemError(status, code=code, detail=detail, extensions=extensions or None)


def _render_problem(exc: RenderFailed) -> ProblemError:
    status = {
        "brand_incomplete": 409,
        "timeout": 504,
        "unavailable": 503,
        "asset_integrity": 500,
    }.get(exc.code, 422)
    detail = exc.detail or "The slides could not be rendered."
    return ProblemError(status, code=exc.code, detail=detail)


def _summary(post: PostRow) -> PostSummary:
    return PostSummary(
        id=post.id,
        status=post.status,
        status_changed_at=post.status_changed_at,
        lane=post.lane,
        pillar=post.pillar,
        format=post.format,
        hook=first_line(post.display_text),
        slot=post.slot,
        go_live_mode=post.go_live_mode,
        kit_state=post.kit.state if post.kit is not None else None,
        approval_current=post.approval_current,
        posted_at=post.posted_at,
        review_only=post.review_only,
        article_id=post.article_id,
        week_plan_id=post.week_plan_id,
        plan_slot_id=post.plan_slot_id,
        updated_at=post.updated_at,
    )


async def _exists[D: (StoryEntry, PovEntry, Idea, QuickSearch)](
    store: Store, cls: type[D], doc_id: str, field: str
) -> D:
    try:
        return (await read_doc(store, cls, doc_id)).doc
    except NotFound:
        raise _problem(
            422, "unknown_reference", "Something this post refers to does not exist.", field=field
        ) from None


async def _require_usable(store: Store, story_ids: list[str], pov_ids: list[str]) -> None:
    """422 ``unusable_material`` for chosen stories or POVs that are not approved with a
    current passing classifier verdict (drafts never use them)."""
    if not story_ids and not pov_ids:
        return
    bank = await load_bank(store, usable_only=True)
    usable = {entry.id for entry in (*bank.stories, *bank.povs)}
    unusable = [entry_id for entry_id in (*story_ids, *pov_ids) if entry_id not in usable]
    if unusable:
        raise _problem(
            422,
            "unusable_material",
            "Approve these entries (and let the classifier clear them) first.",
            ids=list(unusable),
        )


async def _taken_slots(
    store: Store, cadence: CadenceSettings, *, exclude: str, now: datetime
) -> list[Slot]:
    """Every go-live time another post holds (``domain.golive.taken_slots``: approved, staged,
    publishing, needs-attention and kit posts, and posts that went out today; read strictly: an
    unreadable post fails the check). Posts are never stacked, whatever state the other is in."""
    zone = cadence.timezone
    midnight = parse_iso(slot_utc(local_date_time(to_iso(now), zone)[0], "00:00", zone))
    taken = await taken_slots(store, cadence, since=midnight, exclude={exclude})
    return [item.slot for item in taken]


async def _mark_idea_used(store: Store, idea_id: str, post_id: str, stamp: str) -> None:
    """The idea a post starts from becomes ``used`` (when its machine allows it)."""

    def mutate(idea: Idea) -> Idea | None:
        if idea.status is IdeaStatus.USED:
            return None
        if IdeaStatus.USED not in IDEA_MACHINE.edges.get(idea.status, frozenset()):
            return None
        return idea.model_copy(
            update={
                "status": IdeaStatus.USED,
                "status_changed_at": stamp,
                "updated_at": stamp,
                "post_id": post_id,
            }
        )

    try:
        await update_doc(store, Idea, idea_id, mutate)
    except NotFound:
        return


def _validated(post: Post) -> Post:
    """``post`` re-validated after an edit (``model_copy`` skips validation): 422
    ``invalid_post`` instead of a refused write."""
    try:
        return Post.model_validate(post.model_dump(by_alias=True))
    except ValidationError:
        raise _problem(422, "invalid_post", "The edit does not fit a post.") from None


def _slot_of(body: ApproveIn | None, post: Post, cadence: CadenceSettings) -> Slot | None:
    if body is None or body.slot is None:
        return post.slot
    try:
        return body.slot.to_slot(cadence.timezone)
    except (ValueError, ValidationError):
        raise _problem(
            422, "invalid_slot", "That slot is not a valid local date and time."
        ) from None


# --------------------------------------------------------------------------------------------
# The router
# --------------------------------------------------------------------------------------------


def get_router() -> APIRouter:
    """Every linkedin posts route (``/api/posts*``, ``/api/articles/{id}/posts/derive``)."""
    router = APIRouter(tags=["posts"])

    # ---- lists and reads ---------------------------------------------------------------

    @router.get("/api/posts")
    async def list_posts(
        store: StoreDep,
        status: Annotated[list[PostStatus] | None, Query()] = None,
        lane: Annotated[
            list[Literal["derivative", "native", "quick_take", "repurpose"]] | None, Query()
        ] = None,
        pillar: str | None = None,
        article_id: Annotated[str | None, Query(alias="articleId")] = None,
        week_plan_id: Annotated[str | None, Query(alias="weekPlanId")] = None,
        start: Annotated[str | None, Query(alias="from")] = None,
        end: Annotated[str | None, Query(alias="to")] = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> Page[PostSummary]:
        filters: list[Filter] = []
        if status:
            filters.append(Filter("status", "in", [item.value for item in status]))
        if lane:
            filters.append(Filter("lane", "in", list(lane)))
        if pillar:
            filters.append(Filter("pillar", "==", pillar))
        if article_id:
            filters.append(Filter("articleId", "==", article_id))
        if week_plan_id:
            filters.append(Filter("weekPlanId", "==", week_plan_id))
        for name, value in (("from", start), ("to", end)):
            if value is not None and not is_timestamp(value):
                raise _problem(422, "invalid_range", f"`{name}` must be a UTC timestamp.")
        if start is not None:
            filters.append(Filter("slot.utc", ">=", start))
        if end is not None:
            filters.append(Filter("slot.utc", "<", end))
        size = clamp_limit(limit if limit is not None else _DEFAULT_LIMIT)
        with problems():
            if cursor is not None:
                filters.append(Filter("updatedAt", "<", decode_cursor(cursor)))
            found = await find_docs(
                store, Post, filters, order_by=_NEWEST, limit=size + 1, fields=PostRow
            )
        page = found[:size]
        next_cursor = encode_cursor(page[-1].doc.updated_at) if len(found) > size else None
        return Page[PostSummary](
            items=[Versioned[PostSummary](item=_summary(s.doc), etag=s.etag) for s in page],
            next_cursor=next_cursor,
        )

    @router.get("/api/posts/{post_id}")
    async def get_post(post_id: str, store: StoreDep, response: Response) -> Versioned[Post]:
        with problems():
            stored = await read_doc(store, Post, post_id)
        return versioned(stored, response)

    # ---- creating ----------------------------------------------------------------------

    @router.post("/api/posts", status_code=202)
    async def create_post(body: PostCreateIn, services: ServicesDep, ai: AIDep) -> PostCreated:
        del ai  # 503 ai_unavailable when models are not configured
        with problems():
            store = services.store
            now = services.clock.now()
            stamp = to_iso(now)
            policy, _ = await read_singleton(store, EmployerPolicy, now=stamp)
            require_ai_use(policy)
            settings = await load_check_settings(store, now=now)
            check_texts({"takeText": body.take_text}, settings.blocklist)
            if body.take_text is not None:
                # The owner's own words, stored on the post: the blocklist and the blocking
                # entity checks (e-mail, phone, link, IP address, @handle), as for interview
                # answers, before anything is saved.
                entities = [
                    finding
                    for finding in local_findings(body.take_text, settings.blocklist)
                    if finding.severity == "block"
                ]
                if entities:
                    raise ConfidentialText({"takeText": entities})
            pillar = settings.pillars.get(body.pillar)
            if pillar is None:
                raise _problem(422, "unknown_pillar", "Pick a pillar from Library > Pillars.")
            lanes, _ = await read_singleton(store, LaneSettings, now=stamp)
            if body.format == "poll":
                raise _problem(422, "unsupported_format", "Polls are written by hand for now.")
            if body.format == "image_card" and not lanes.formats.image_card_enabled:
                raise _problem(422, "format_disabled", "Image cards are switched off.")
            for index, story_id in enumerate(body.story_ids):
                await _exists(store, StoryEntry, story_id, f"storyIds[{index}]")
            for index, pov_id in enumerate(body.pov_ids):
                await _exists(store, PovEntry, pov_id, f"povIds[{index}]")
            await _require_usable(store, body.story_ids, body.pov_ids)
            idea = (
                await _exists(store, Idea, body.idea_id, "ideaId")
                if body.idea_id is not None
                else None
            )
            if body.quick_search_id is not None:
                await _exists(store, QuickSearch, body.quick_search_id, "quickSearchId")
            if body.lane == "quick_take":
                if idea is None:
                    raise _problem(422, "idea_required", "A quick take starts from an idea.")
                if lanes.quick_take.owner_take_required and not (body.take_text or body.pov_ids):
                    raise _problem(422, "owner_take_required", "Add your take first.")
            if body.lane == "repurpose" and (idea is None or idea.repurpose_of is None):
                raise _problem(
                    422, "repurpose_source_missing", "Repurposing starts from a repurpose idea."
                )
            cadence, _ = await read_singleton(store, CadenceSettings, now=stamp)
            slot = None
            if body.slot is not None and body.plan_slot is not None:
                raise _problem(
                    422, "invalid_slot", "Give the plan's slot or a time, not both.", field="slot"
                )
            if body.slot is not None:
                try:
                    slot = body.slot.to_slot(cadence.timezone)
                except (ValueError, ValidationError):
                    raise _problem(422, "invalid_slot", "That slot is not valid.") from None
            planned: tuple[WeekPlan, PlannedSlot] | None = None
            if body.plan_slot is not None:
                try:
                    planned = await plan_slot_for(
                        store, body.plan_slot.week_start, body.plan_slot.slot_id, lane=body.lane
                    )
                except PlanSlotRefused as exc:
                    raise _problem(exc.status, exc.code, exc.detail, field="planSlot") from None
                slot = planned[1].slot
            expires_at: str | None = None
            if idea is not None and idea.time_sensitive:
                expires_at = idea.expires_at or to_iso(
                    now + timedelta(hours=lanes.quick_take.max_age_hours)
                )
            post = Post(
                id=Post.new_id(now=now),
                created_at=stamp,
                updated_at=stamp,
                status=PostStatus.GENERATING,
                status_changed_at=stamp,
                lane=body.lane,
                pillar=body.pillar,
                format=body.format,
                story_ids=list(dict.fromkeys(body.story_ids)),
                pov_ids=list(dict.fromkeys(body.pov_ids)),
                idea_id=body.idea_id,
                quick_search_id=body.quick_search_id,
                week_plan_id=planned[0].id if planned is not None else None,
                plan_slot_id=planned[1].id if planned is not None else None,
                slot=slot,
                go_live_mode=body.go_live_mode or await default_go_live_mode(store, now=now),
                time_sensitive=expires_at is not None,
                expires_at=expires_at,
            )
            if body.take_text is not None:
                post = with_extra(post, ownerTake=body.take_text.strip())
            created = await create_doc(store, post)
            if planned is not None:
                # The post carries its link already; the plan's slot names it too (one field,
                # no status change: an approved plan stays approved). A slot whose earlier post
                # is gone (archived, expired) takes the new one in its place.
                await link_plan_slot(
                    store,
                    planned[0].id,
                    planned[1].id,
                    post.id,
                    now=now,
                    replaces=planned[1].post_id,
                )
            if body.idea_id is not None:
                await _mark_idea_used(store, body.idea_id, post.id, stamp)
            job = await services.jobs.enqueue(
                POSTS_GENERATE, GenerateInput(post_id=post.id, origin="owner").to_json()
            )
            if expires_at is not None:
                await enqueue_once(
                    services.jobs,
                    POSTS_EXPIRE,
                    PostJobInput(post_id=post.id, origin="owner").to_json(),
                    job_id=expire_job_id(post.id),
                    run_after=parse_iso(expires_at),
                )
        logger.info("posts_created", extra={"post_id": post.id, "post_lane": post.lane})
        return PostCreated(post=versioned(created), job_id=job.id)

    @router.post("/api/articles/{article_id}/posts/derive", status_code=202)
    async def derive_posts(article_id: str, services: ServicesDep, ai: AIDep) -> JobAccepted:
        del ai
        with problems():
            store = services.store
            stamp = to_iso(services.clock.now())
            article = (await read_doc(store, Article, article_id)).doc
            if article.status not in DERIVABLE_STATUSES:
                raise _problem(
                    409,
                    "article_not_ready",
                    "Derive posts from an article in review, approved or published.",
                )
            if article.meta.pillar is None:
                raise _problem(
                    409, "meta_incomplete", "Set the article's pillar first.", missing=["pillar"]
                )
            policy, _ = await read_singleton(store, EmployerPolicy, now=stamp)
            require_ai_use(policy)
            settings = await load_check_settings(store, now=services.clock.now())
            if not settings.blocklist.configured:
                raise BlocklistNotConfigured()
            job = await enqueue_once(
                services.jobs,
                POSTS_DERIVE,
                ArticleJobInput(article_id=article.id, origin="owner").to_json(),
                job_id=derive_job_id(article.id, article.version),
                on_conflict="rerun",
            )
        return JobAccepted(job_id=job.id, status=job.status)

    # ---- editing -----------------------------------------------------------------------

    @router.put("/api/posts/{post_id}")
    async def edit_post(
        post_id: str,
        body: PostEditIn,
        services: ServicesDep,
        if_match: IfMatch,
        response: Response,
    ) -> Versioned[Post]:
        with problems():
            store = services.store
            now = services.clock.now()
            stamp = to_iso(now)
            before = (await _current(store, post_id, if_match)).doc
            _editable(before)
            settings = await load_check_settings(store, now=now)
            update = _edit(before, body, settings.pillars, stamp)
            changed = _validated(before.model_copy(update={**update, **_to_draft(before, stamp)}))
            changed = changed.model_copy(
                update={"lint": await store_lint_state(services, changed, settings, now=now)}
            )
            stored = await replace_doc(store, changed, if_match=if_match)
            await _after_unapproval(services, before)
        if body.link_exception is not None:
            logger.info("posts_link_exception", extra={"post_id": post_id})
        return versioned(stored, response)

    @router.post("/api/posts/{post_id}/lint")
    async def lint_text(post_id: str, body: LintIn, services: ServicesDep) -> LintOut:
        with problems():
            store = services.store
            now = services.clock.now()
            post = (await read_doc(store, Post, post_id)).doc
            settings = await load_check_settings(store, now=now)
        try:
            display, payload = serialize(body.display_text, post.hashtags)
        except LittleTextError:
            display, payload = body.display_text, ""
        candidate = post.model_copy(update={"display_text": display, "payload": payload})
        findings = [
            *lint_post_text(
                candidate,
                hashtags=post.hashtags,
                blocklist=settings.blocklist,
                slop=services.slop,
                catalog=settings.catalog,
                pillars=settings.pillars,
                lanes=settings.lanes,
                now=now,
            ),
            *forbidden_topic_post_findings(candidate, settings.policy),
        ]
        return LintOut(
            findings=findings, payload_units=payload_units(payload), fold_index=fold_index(display)
        )

    @router.post("/api/posts/{post_id}/check", status_code=202)
    async def check_post_route(post_id: str, services: ServicesDep) -> JobAccepted:
        with problems():
            post = (await read_doc(services.store, Post, post_id)).doc
            if post.status is PostStatus.GENERATING:
                raise _problem(409, "post_generating", "Wait until the post is written.")
            job = await services.jobs.enqueue(
                POSTS_CHECK, PostJobInput(post_id=post.id, origin="owner").to_json()
            )
        return JobAccepted(job_id=job.id, status=job.status)

    @router.put("/api/posts/{post_id}/carousel")
    async def put_carousel(
        post_id: str,
        body: CarouselIn,
        services: ServicesDep,
        if_match: IfMatch,
        response: Response,
    ) -> Versioned[Post]:
        with problems():
            store = services.store
            now = services.clock.now()
            stamp = to_iso(now)
            before = (await _current(store, post_id, if_match)).doc
            _editable(before)
            if before.format not in MEDIA_FORMATS:
                raise _problem(409, "not_a_carousel", "Only carousels and image cards have slides.")
            try:
                spec = parse_spec(body.spec)
            except InvalidSpec as exc:
                raise _problem(
                    422, InvalidSpec.code, "The slides are not valid.", errors=exc.errors
                ) from None
            if before.format == "image_card" and len(spec.slides) != 1:
                raise _problem(422, InvalidSpec.code, "An image card has exactly one slide.")
            update: dict[str, object] = {
                "carousel_spec": spec_json(spec),
                "media": [],
                **_to_draft(before, stamp),
            }
            if before.format == "carousel":
                update["document_title"] = spec.title
            changed = before.model_copy(update=update)
            settings = await load_check_settings(store, now=now)
            changed = changed.model_copy(
                update={"lint": await store_lint_state(services, changed, settings, now=now)}
            )
            stored = await replace_doc(store, changed, if_match=if_match)
            await _after_unapproval(services, before)
        return versioned(stored, response)

    @router.get(
        "/api/posts/{post_id}/carousel/preview",
        response_class=HTMLResponse,
        responses={200: {"description": "The slides as the PDF is printed from them (HTML)."}},
    )
    async def carousel_preview(post_id: str, services: ServicesDep) -> HTMLResponse:
        with problems():
            store = services.store
            stamp = to_iso(services.clock.now())
            post = (await read_doc(store, Post, post_id)).doc
            if post.carousel_spec is None:
                raise NotFound(f"posts/{post_id}: no slides")
            render = services.render
            if render is None:
                raise _problem(503, "render_unavailable", "Rendering is not configured.")
            brand, _ = await read_singleton(store, BrandKit, now=stamp)
            policy, _ = await read_singleton(store, EmployerPolicy, now=stamp)
        try:
            html = await render.carousel_preview_html(
                post.carousel_spec, brand, disclaimer=policy.disclaimer_short
            )
        except RenderFailed as exc:
            raise _render_problem(exc) from None
        return HTMLResponse(html, headers=dict(PREVIEW_HEADERS))

    @router.post("/api/posts/{post_id}/render", status_code=202)
    async def render_post(post_id: str, services: ServicesDep) -> JobAccepted:
        with problems():
            if services.render is None:
                raise _problem(503, "render_unavailable", "Rendering is not configured.")
            if services.blobs is None:
                raise _problem(503, "storage_unavailable", "Storage is not configured.")
            post = (await read_doc(services.store, Post, post_id)).doc
            if post.format not in MEDIA_FORMATS or post.carousel_spec is None:
                raise _problem(409, "nothing_to_render", "Add the slides first.")
            job = await services.jobs.enqueue(
                POSTS_RENDER, PostJobInput(post_id=post.id, origin="owner").to_json()
            )
        return JobAccepted(job_id=job.id, status=job.status)

    @router.post("/api/posts/{post_id}/regenerate", status_code=202)
    async def regenerate(
        post_id: str,
        body: RegenerateIn,
        services: ServicesDep,
        ai: AIDep,
        if_match: IfMatch,
    ) -> JobAccepted:
        del ai
        with problems():
            store = services.store
            now = services.clock.now()
            stamp = to_iso(now)
            before = (await _current(store, post_id, if_match)).doc
            _editable(before)
            if body.what == "carousel" and before.format not in MEDIA_FORMATS:
                raise _problem(409, "not_a_carousel", "Only carousels and image cards have slides.")
            policy, _ = await read_singleton(store, EmployerPolicy, now=stamp)
            require_ai_use(policy)
            settings = await load_check_settings(store, now=now)
            check_texts(
                {
                    "instruction": body.instruction,
                    "displayText": before.display_text,
                    "documentTitle": before.document_title,
                },
                settings.blocklist,
            )
            drafted = before.model_copy(update=_to_draft(before, stamp))
            target = POST_MACHINE.transition(drafted.status, PostStatus.GENERATING)
            changed = drafted.model_copy(
                update={"status": target, "status_changed_at": stamp, "updated_at": stamp}
            )
            await replace_doc(store, changed, if_match=if_match)
            await _after_unapproval(services, before)
            job = await services.jobs.enqueue(
                POSTS_GENERATE,
                GenerateInput(
                    post_id=before.id, origin="owner", what=body.what, instruction=body.instruction
                ).to_json(),
            )
        return JobAccepted(job_id=job.id, status=job.status)

    # ---- approval ----------------------------------------------------------------------

    @router.post("/api/posts/{post_id}/approve")
    async def approve(
        post_id: str,
        services: ServicesDep,
        if_match: IfMatch,
        response: Response,
        body: Annotated[ApproveIn | None, Body()] = None,
    ) -> Versioned[Post]:
        with problems():
            stored = await _approve(services, post_id, body, if_match=if_match)
        return versioned(stored, response)

    @router.post("/api/posts/{post_id}/unapprove")
    async def unapprove(
        post_id: str, services: ServicesDep, if_match: IfMatch, response: Response
    ) -> Versioned[Post]:
        with problems():
            stamp = to_iso(services.clock.now())
            before = (await _current(services.store, post_id, if_match)).doc
            if before.status not in (PostStatus.APPROVED, PostStatus.STAGED):
                POST_MACHINE.transition(before.status, PostStatus.DRAFT)
                raise _problem(409, "not_approved", "Only an approved post can be unapproved.")
            changed = before.model_copy(update=_to_draft(before, stamp))
            stored = await replace_doc(services.store, changed, if_match=if_match)
            await _after_unapproval(services, before)
        return versioned(stored, response)

    @router.post("/api/posts/{post_id}/archive")
    async def archive(
        post_id: str, services: ServicesDep, if_match: IfMatch, response: Response
    ) -> Versioned[Post]:
        with problems():
            stamp = to_iso(services.clock.now())
            before = (await _current(services.store, post_id, if_match)).doc
            target = POST_MACHINE.transition(before.status, PostStatus.ARCHIVED)
            changed = before.model_copy(
                update={
                    "status": target,
                    "status_changed_at": stamp,
                    "updated_at": stamp,
                    "staging": None,
                }
            )
            stored = await replace_doc(services.store, changed, if_match=if_match)
            await _after_unapproval(services, before)
        return versioned(stored, response)

    return router


# --------------------------------------------------------------------------------------------
# Edit and approve logic (kept out of the closures so they stay readable)
# --------------------------------------------------------------------------------------------


def _edit(before: Post, body: PostEditIn, pillars: PillarSettings, stamp: str) -> dict[str, object]:
    """The field changes of an owner edit (validated; 422 problems for bad references)."""
    update: dict[str, object] = {}
    pillar_id = body.pillar or before.pillar
    pillar = pillars.get(pillar_id)
    if pillar is None:
        raise _problem(422, "unknown_pillar", "Pick a pillar from Library > Pillars.")
    if body.pillar is not None:
        update["pillar"] = body.pillar
    if body.hashtags is not None:
        hashtags = approved_subset(body.hashtags, pillar)
        unknown = [
            tag
            for tag in body.hashtags
            if tag.strip().removeprefix("#").casefold() not in {h.casefold() for h in hashtags}
        ]
        if unknown:
            raise _problem(
                422,
                "hashtag_not_approved",
                "Use the pillar's approved hashtags only.",
                hashtags=list(unknown),
            )
    else:
        hashtags = approved_subset(before.hashtags, pillar)
    update["hashtags"] = hashtags
    try:
        display, payload = serialize(body.display_text, hashtags)
    except LittleTextError:
        raise _problem(
            422, "invalid_text", "The text has characters LinkedIn cannot take."
        ) from None
    update["display_text"] = display
    update["payload"] = payload
    if body.selected_hook_id is not None:
        variant = next((v for v in before.hook_variants if v.id == body.selected_hook_id), None)
        if variant is None:
            raise _problem(422, "unknown_hook", "Pick one of the hook variants.")
        update["selected_hook_id"] = variant.id
        update["hook_type"] = variant.hook_type
    fmt = body.format or before.format
    if body.format is not None and body.format != before.format:
        if body.format == "poll":
            raise _problem(422, "unsupported_format", "Polls are written by hand for now.")
        update["format"] = body.format
        update["media"] = []
    if body.document_title is not None:
        update["document_title"] = body.document_title
        if before.carousel_spec is not None and fmt in MEDIA_FORMATS:
            try:
                update["carousel_spec"] = spec_with_title(before.carousel_spec, body.document_title)
            except InvalidSpec:
                raise _problem(422, "invalid_spec", "That title does not fit the slides.") from None
            update["media"] = []
    if body.attributions is not None:
        update["attributions"] = [
            Attribution(publisher=a.publisher, title=a.title, citation_id=a.citation_id)
            for a in body.attributions
        ]
    if "link_exception" in body.model_fields_set:
        link = body.link_exception
        if link is None:
            update["link_exception"] = None
        else:
            previous = before.link_exception
            unchanged = (
                previous is not None and previous.url == link.url and previous.reason == link.reason
            )
            update["link_exception"] = (
                previous
                if unchanged
                else LinkException(url=link.url, reason=link.reason, approved_at=stamp)
            )
    return update


async def _approve(
    services: Services, post_id: str, body: ApproveIn | None, *, if_match: str
) -> Stored[Post]:
    store = services.store
    now = services.clock.now()
    stamp = to_iso(now)
    post = (await _current(store, post_id, if_match)).doc
    if post.status is not PostStatus.DRAFT:
        # Other edges into approved belong to the planner (re-slot) and the transport.
        allowed = POST_MACHINE.edges.get(post.status, frozenset[PostStatus]())
        raise TransitionError(
            POST_MACHINE.name,
            post.status.value,
            PostStatus.APPROVED.value,
            [state.value for state in allowed if state is not PostStatus.APPROVED],
        )
    target = POST_MACHINE.transition(post.status, PostStatus.APPROVED)
    if post.article_id is not None:
        # Posts derived while the flagship was still in review are previews: they go out only
        # after the owner approves the article itself.
        try:
            source = (await read_doc(store, Article, post.article_id)).doc
        except NotFound:
            raise _problem(409, "article_missing", "The source article no longer exists.") from None
        if source.status not in APPROVED_ARTICLE_STATUSES:
            raise _problem(
                409,
                "article_not_approved",
                "Approve the article first; posts derived from it can be approved after that.",
                articleId=source.id,
            )
    settings = await load_check_settings(store, now=now)
    if not settings.blocklist.configured:
        raise BlocklistNotConfigured()
    cadence, _ = await read_singleton(store, CadenceSettings, now=stamp)
    slot = _slot_of(body, post, cadence)
    if slot is not None:
        _check_slot_time(slot, post, now)
        taken = await _taken_slots(store, cadence, exclude=post.id, now=now)
        conflicts = conflicting_slots(slot, taken, cadence=cadence)
        if conflicts:
            raise _problem(
                409,
                "slot_conflict",
                "Another post is planned that day. Pick another slot.",
                conflicts=[other.utc for other in conflicts],
            )
    if not payload_matches(post):
        raise _problem(409, "payload_mismatch", "The payload does not match the text; save again.")
    if media_missing(post):
        raise _problem(409, "media_missing", "Render the slides before approving.")
    candidate = post.model_copy(update={"slot": slot})
    candidate = candidate.model_copy(
        update={
            "lint": await store_lint_state(services, candidate, settings, now=now),
            "near_duplicate": await near_duplicate_state(
                store,
                candidate,
                threshold=settings.preferences.near_duplicate_max_similarity,
                now=now,
            ),
        }
    )
    failing = failing_checks(candidate)
    if failing:
        raise _problem(
            409,
            "checks_failing",
            "Some checks fail or are out of date. Run the checks and fix the findings.",
            failing=list(failing),
        )
    changes: dict[str, object] = {
        "status": target,
        "status_changed_at": stamp,
        "updated_at": stamp,
        "approval": Approval(sha256=candidate.compute_approval_hash(), approved_at=stamp),
        "staging": None,
        "last_error": None,
    }
    approved = candidate.model_copy(update=changes)
    if approved.go_live_mode == "kit":
        # Kit mode (the default while LinkedIn is not connected) has no staging or tap: the
        # post goes straight to the kit, prepared, as switching it there would (approved ->
        # kit, ``domain.posts.into_kit``), so Today shows it at its slot and the kit view
        # serves it.
        approved = into_kit(approved, stamp=stamp)
    stored = await replace_doc(store, approved, if_match=if_match)
    await enqueue_slot_jobs(services.jobs, stored.doc, cadence=cadence)
    logger.info("posts_approved", extra={"post_id": post_id})
    return stored


def _check_slot_time(slot: Slot, post: Post, now: datetime) -> None:
    if parse_iso(slot.utc) <= now:
        raise _problem(422, "slot_in_past", "That slot has passed. Pick a later one.")
    if post.time_sensitive and post.expires_at is not None and slot.utc > post.expires_at:
        raise _problem(
            409, "slot_after_expiry", "This post expires before that slot. Pick an earlier one."
        )
