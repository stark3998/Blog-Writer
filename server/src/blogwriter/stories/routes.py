"""HTTP routes of the story bank, the POV bank, intake, seeding and classification (W-A3).

Every route is owner-only and every non-GET passed CSRF (core). Intake and every save answer
409 ``blocklist_not_configured`` until the owner saved the blocklist once (fail closed).
Saves take **owner-confirmed** wording only and refuse blocking local findings with 422
``confidential_text`` (``findings[]`` of ``{field, code, severity, message, start, end, ref}``,
term ids only). Raw intake text exists only in ``POST /api/stories/intake/redact`` and its
anonymizer call; responses are ``Cache-Control: no-store`` (core's default for ``/api``).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Annotated, Final

from fastapi import APIRouter, Query, Request, Response
from pydantic import ValidationError

from blogwriter.ai.ledger import Origin
from blogwriter.core.errors import JsonValue, ProblemError
from blogwriter.core.logging import describe_exception
from blogwriter.db import Conflict, Filter, NotFound, OrderBy, Store
from blogwriter.domain.api import (
    JobAccepted,
    Page,
    Versioned,
    clamp_limit,
    decode_cursor,
    encode_cursor,
)
from blogwriter.domain.base import to_iso
from blogwriter.domain.common import ClassifierCheck, Finding
from blogwriter.domain.confidentiality import (
    BlocklistNotConfigured,
    check_texts,
    require_ai_use,
)
from blogwriter.domain.deps import (
    ClockDep,
    IfMatch,
    JobsDep,
    ServicesDep,
    StoreDep,
    etag_mismatch,
    problems,
    versioned,
)
from blogwriter.domain.docstore import (
    Stored,
    create_doc,
    delete_doc,
    find_docs,
    read_doc,
    read_singleton,
    replace_doc,
)
from blogwriter.domain.ephemeral import SeedText
from blogwriter.domain.jobtypes import STORIES_CLASSIFY, STORIES_SEED
from blogwriter.domain.ports import Services, ServiceUnavailable
from blogwriter.domain.settings import Blocklist, EmployerPolicy, PillarSettings
from blogwriter.domain.signals import Idea
from blogwriter.domain.states import REVIEW_MACHINE, ReviewStatus, transition
from blogwriter.domain.stories import PovEntry, StoryEntry, StoryMetric
from blogwriter.stories._logging import logger
from blogwriter.stories.bank import (
    entry_text,
    pov_text_fields,
    story_text_fields,
    verdict_current,
)
from blogwriter.stories.intake import (
    RedactionProposal,
    local_findings,
    propose,
    public_terms,
)
from blogwriter.stories.jobs import ClassifyJobInput, SeedJobInput
from blogwriter.stories.models import (
    CheckIn,
    CheckOut,
    MetricIn,
    PovIn,
    RedactIn,
    ReviewDecision,
    ReviewIn,
    SeedIn,
    SeedSource,
    StoryIn,
)
from blogwriter.stories.settings import StoriesSettings, get_settings

__all__ = ["get_router"]

_SCAN_BATCH: Final = 100
_MAX_SCAN_ROUNDS: Final = 10
_RAW_SEED_TYPES: Final = frozenset(
    {
        "text/plain",
        "text/markdown",
        "text/x-markdown",
        "text/mdx",
        "text/x-tex",
        "application/x-tex",
        "text/x-latex",
        "application/x-latex",
    }
)
_NEWEST: Final = OrderBy("updatedAt", descending=True)


# --------------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------------


def _stamp(clock_now: datetime) -> str:
    return to_iso(clock_now)


def _too_long(field: str, limit: int) -> ProblemError:
    return ProblemError(
        422,
        code="validation_failed",
        extensions={
            "errors": [
                {
                    "loc": ["body", field],
                    "msg": f"String should have at most {limit} characters",
                    "type": "string_too_long",
                }
            ]
        },
    )


def _confidential(findings: Mapping[str, Sequence[Finding]]) -> ProblemError:
    items: list[JsonValue] = [
        {"field": field, **finding.model_dump(mode="json", exclude_none=True)}
        for field, found in findings.items()
        for finding in found
    ]
    return ProblemError(
        422,
        code="confidential_text",
        detail="Some text needs redacting before it can be saved. Check the highlighted parts.",
        extensions={"findings": items},
    )


async def _blocklist(store: Store, stamp: str) -> Blocklist:
    """The saved blocklist; 409 ``blocklist_not_configured`` until the owner saved it once."""
    blocklist, _ = await read_singleton(store, Blocklist, now=stamp)
    if not blocklist.configured:
        raise BlocklistNotConfigured()
    return blocklist


def _refuse_blocking(fields: Mapping[str, str], blocklist: Blocklist, terms: Sequence[str]) -> None:
    """422 ``confidential_text`` when any field has a blocking local finding."""
    blocking: dict[str, list[Finding]] = {}
    for name, text in fields.items():
        found = [
            finding
            for finding in local_findings(text, blocklist, public_terms=terms)
            if finding.severity == "block"
        ]
        if found:
            blocking[name] = found
    if blocking:
        raise _confidential(blocking)


async def _pillars(store: Store, stamp: str) -> PillarSettings:
    pillars, _ = await read_singleton(store, PillarSettings, now=stamp)
    return pillars


def _check_pillars(pillar_ids: Sequence[str], pillars: PillarSettings) -> None:
    known = {pillar.id for pillar in pillars.pillars}
    unknown = [pid for pid in pillar_ids if pid not in known]
    if unknown:
        raise ProblemError(
            422,
            code="unknown_pillar",
            detail="Pick pillars from Library > Pillars.",
            extensions={"pillars": list(unknown)},
        )


async def _classify_now(services: Services, text: str, stamp: str) -> ClassifierCheck | None:
    """Run the classifier on owner-confirmed text when it can run; ``None`` leaves the entry
    unchecked (not usable until ``stories.classify``). Never blocks the save."""
    classifier = services.classifier
    if classifier is None or services.ai is None:
        return None
    policy, _ = await read_singleton(services.store, EmployerPolicy, now=stamp)
    if not policy.ai_use_confirmed:
        return None
    try:
        checks = await classifier.classify([text], origin="owner")
    except Exception as exc:
        logger.warning("stories_classifier_unavailable", extra=describe_exception(exc))
        return None
    if len(checks) != 1:
        return None
    return checks[0]


def _metrics(items: Sequence[MetricIn]) -> list[StoryMetric]:
    return [
        StoryMetric(
            label=item.label,
            value=item.value,
            bucketed=item.bucketed,
            public_source=item.public_source,
        )
        for item in items
    ]


def _review_target(current: ReviewStatus, decision: ReviewDecision) -> ReviewStatus:
    match decision:
        case "approve":
            return ReviewStatus.APPROVED
        case "reject":
            return ReviewStatus.REJECTED
        case "archive":
            return ReviewStatus.ARCHIVED
        case "restore":
            if current is ReviewStatus.ARCHIVED:
                return ReviewStatus.APPROVED
            return ReviewStatus.PROPOSED


def _reviewed[E: (StoryEntry, PovEntry)](entry: E, decision: ReviewDecision, stamp: str) -> E:
    target = _review_target(entry.status, decision)
    transition(REVIEW_MACHINE, entry.status, target)
    update: dict[str, object] = {
        "status": target,
        "status_changed_at": stamp,
        "updated_at": stamp,
    }
    if target is ReviewStatus.APPROVED:
        update["approved_at"] = stamp
    return entry.model_copy(update=update)


async def _current[D: (StoryEntry, PovEntry)](
    store: Store, cls: type[D], doc_id: str, if_match: str
) -> Stored[D]:
    current = await read_doc(store, cls, doc_id)
    if current.etag != if_match:
        raise etag_mismatch(current)
    return current


def _after_edit[E: (StoryEntry, PovEntry)](entry: E, check: ClassifierCheck | None) -> E:
    """A fresh verdict, or the old one if it still matches the text, else "not checked"."""
    if check is not None:
        return entry.model_copy(update={"classifier": check})
    if verdict_current(entry.classifier, entry_text(entry)):
        return entry
    return entry.model_copy(update={"classifier": ClassifierCheck()})


async def _read_body(request: Request, max_bytes: int) -> bytes:
    """The request body, refused with 413 ``too_large`` past ``max_bytes`` (never unbounded)."""
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > max_bytes:
        raise ProblemError(413, code="too_large", detail="The text is too long.")
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > max_bytes:
            raise ProblemError(413, code="too_large", detail="The text is too long.")
        chunks.append(chunk)
    return b"".join(chunks)


def _validation_problem(exc: ValidationError) -> ProblemError:
    errors: list[JsonValue] = []
    for error in exc.errors(include_url=False, include_context=False, include_input=False):
        loc: list[JsonValue] = ["body"]
        loc.extend(error["loc"])
        errors.append({"loc": loc, "msg": error["msg"], "type": error["type"]})
    return ProblemError(422, code="validation_failed", extensions={"errors": errors})


def _media_type(request: Request) -> tuple[str, str | None]:
    raw = request.headers.get("content-type", "")
    parts = [part.strip() for part in raw.split(";")]
    charset: str | None = None
    for part in parts[1:]:
        name, _, value = part.partition("=")
        if name.strip().lower() == "charset":
            charset = value.strip().strip('"').lower()
    return parts[0].lower(), charset


async def _seed_body(
    request: Request, source: SeedSource | None, settings: StoriesSettings
) -> SeedIn:
    """The seed text from a JSON ``SeedIn`` body or a raw text body with ``?source=``."""
    media, charset = _media_type(request)
    limit = settings.seed_max_chars
    if media == "application/json":
        raw = await _read_body(request, limit * 6 + 1024)
        try:
            body = SeedIn.model_validate_json(raw)
        except ValidationError as exc:
            raise _validation_problem(exc) from None
    elif media in _RAW_SEED_TYPES:
        if charset not in (None, "utf-8", "utf8", "us-ascii"):
            raise ProblemError(415, code="unsupported_media_type", detail="Send UTF-8 text.")
        if source is None:
            raise ProblemError(
                422, code="source_required", detail="Say whether this is a resume or portfolio."
            )
        raw = await _read_body(request, limit * 4 + 4)
        try:
            text = raw.decode("utf-8").removeprefix("\ufeff")
        except UnicodeDecodeError:
            raise ProblemError(
                415, code="unsupported_media_type", detail="Send UTF-8 text."
            ) from None
        if not text.strip():
            raise ProblemError(422, code="validation_failed", detail="The text is empty.")
        body = SeedIn(text=text, source=source)
    else:
        raise ProblemError(
            415,
            code="unsupported_media_type",
            detail="Send JSON, or the text itself as text/plain, text/markdown or LaTeX.",
        )
    if len(body.text) > limit:
        raise _too_long("text", limit)
    if not body.text.strip():
        raise ProblemError(422, code="validation_failed", detail="The text is empty.")
    return body


async def _page_of[D: (StoryEntry, PovEntry)](
    store: Store,
    cls: type[D],
    filters: Sequence[Filter],
    *,
    limit: int | None,
    cursor: str | None,
    keep: Callable[[D], bool] | None = None,
) -> tuple[list[Stored[D]], str | None]:
    """A page newest first (``updatedAt`` keyset). ``keep`` filters what the store cannot
    (array membership, computed flags); scanning stops after a bounded number of rounds and
    the cursor then points past what was scanned."""
    size = clamp_limit(limit)
    after = decode_cursor(cursor) if cursor is not None else None
    items: list[Stored[D]] = []
    batch = size + 1 if keep is None else max(_SCAN_BATCH, size + 1)
    for _ in range(_MAX_SCAN_ROUNDS):
        page_filters = [*filters]
        if after is not None:
            page_filters.append(Filter("updatedAt", "<", after))
        found = await find_docs(store, cls, page_filters, order_by=_NEWEST, limit=batch)
        for stored in found:
            if keep is None or keep(stored.doc):
                items.append(stored)
                if len(items) > size:
                    return items[:size], encode_cursor(items[size - 1].doc.updated_at)
        if len(found) < batch:
            return items, None
        after = found[-1].doc.updated_at
    return items, encode_cursor(after) if after is not None else None


# --------------------------------------------------------------------------------------------
# The router
# --------------------------------------------------------------------------------------------


def get_router(settings: StoriesSettings | None = None) -> APIRouter:
    """Every stories route (``/api/stories*``, ``/api/povs*``)."""
    config = settings or get_settings()
    router = APIRouter(tags=["stories"])

    # ---- intake ------------------------------------------------------------------------

    @router.post("/api/stories/intake/redact")
    async def redact_intake(body: RedactIn, services: ServicesDep) -> RedactionProposal:
        if len(body.text) > config.intake_max_chars:
            raise _too_long("text", config.intake_max_chars)
        with problems():
            return await propose(
                body.text, purpose=body.purpose, services=services, pillar=body.pillar
            )

    @router.post("/api/stories/intake/check")
    async def check_intake(body: CheckIn, store: StoreDep, clock: ClockDep) -> CheckOut:
        if len(body.text) > config.intake_max_chars:
            raise _too_long("text", config.intake_max_chars)
        with problems():
            stamp = _stamp(clock.now())
            blocklist = await _blocklist(store, stamp)
            terms = public_terms(await _pillars(store, stamp), blocklist, None)
            findings = local_findings(body.text, blocklist, public_terms=terms)
        ok = not any(finding.severity == "block" for finding in findings)
        return CheckOut(ok=ok, findings=findings)

    # ---- seeding and classification ----------------------------------------------------

    @router.post(
        "/api/stories/seed",
        status_code=202,
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {"schema": SeedIn.model_json_schema(by_alias=True)},
                    "text/plain": {"schema": {"type": "string"}},
                    "text/markdown": {"schema": {"type": "string"}},
                    "application/x-tex": {"schema": {"type": "string"}},
                },
            }
        },
    )
    async def seed_stories(
        request: Request,
        store: StoreDep,
        jobs: JobsDep,
        clock: ClockDep,
        source: Annotated[SeedSource | None, Query()] = None,
    ) -> JobAccepted:
        """Propose stories and POVs from public resume or portfolio text (owner approves each).

        The body is ``SeedIn`` JSON, or the text itself (``text/plain``, ``text/markdown``,
        LaTeX) with ``?source=cv|portfolio``; no multipart. The text is kept as an ephemeral
        ``SeedText`` (1 day) and the job input holds only its id.
        """
        body = await _seed_body(request, source, config)
        with problems():
            now = clock.now()
            stamp = _stamp(now)
            blocklist, _ = await read_singleton(store, Blocklist, now=stamp)
            check_texts({"text": body.text}, blocklist)
            policy, _ = await read_singleton(store, EmployerPolicy, now=stamp)
            require_ai_use(policy)
            seed = SeedText.issue(body.text, body.source, now=now)
            try:
                await create_doc(store, seed)
            except Conflict:
                existing = await read_doc(store, SeedText, seed.id)
                if existing.doc.expired(now) or existing.doc.source != body.source:
                    await replace_doc(store, seed, if_match=existing.etag)
            job_input = SeedJobInput(seed_id=seed.id, source=body.source, origin="owner")
            job = await jobs.enqueue(STORIES_SEED, job_input.to_json())
        return JobAccepted(job_id=job.id, status=job.status)

    @router.post("/api/stories/classify", status_code=202)
    async def classify_stories(
        services: ServicesDep, store: StoreDep, jobs: JobsDep, clock: ClockDep
    ) -> JobAccepted:
        """Run the classifier over every story and POV whose verdict is missing or stale."""
        with problems():
            policy, _ = await read_singleton(store, EmployerPolicy, now=_stamp(clock.now()))
            require_ai_use(policy)
            if services.classifier is None:
                raise ServiceUnavailable("classifier")
            if services.ai is None:
                raise ServiceUnavailable("ai")
            origin: Origin = "owner"
            job = await jobs.enqueue(STORIES_CLASSIFY, ClassifyJobInput(origin=origin).to_json())
        return JobAccepted(job_id=job.id, status=job.status)

    # ---- stories -------------------------------------------------------------------------

    @router.get("/api/stories")
    async def list_stories(
        store: StoreDep,
        status: Annotated[ReviewStatus | None, Query()] = None,
        pillar: Annotated[str | None, Query(max_length=40)] = None,
        usable: Annotated[bool | None, Query()] = None,
        limit: Annotated[int | None, Query()] = None,
        cursor: Annotated[str | None, Query(max_length=512)] = None,
    ) -> Page[StoryEntry]:
        filters: list[Filter] = []
        if status is not None:
            filters.append(Filter("status", "==", status.value))
        if usable is True:
            filters.append(Filter("status", "==", ReviewStatus.APPROVED.value))
            filters.append(Filter("classifier.passed", "==", True))

        def keep(story: StoryEntry) -> bool:
            if pillar is not None and pillar not in story.pillars:
                return False
            if usable is not None:
                current = story.usable and verdict_current(story.classifier, entry_text(story))
                if current != usable:
                    return False
            return True

        needs_scan = pillar is not None or usable is not None
        with problems():
            items, next_cursor = await _page_of(
                store,
                StoryEntry,
                filters,
                limit=limit,
                cursor=cursor,
                keep=keep if needs_scan else None,
            )
        return Page[StoryEntry](items=[versioned(s) for s in items], next_cursor=next_cursor)

    @router.post("/api/stories", status_code=201)
    async def create_story(
        body: StoryIn, services: ServicesDep, store: StoreDep, clock: ClockDep, response: Response
    ) -> Versioned[StoryEntry]:
        with problems():
            now = clock.now()
            stamp = _stamp(now)
            blocklist = await _blocklist(store, stamp)
            pillars = await _pillars(store, stamp)
            _check_pillars(body.pillars, pillars)
            metrics = _metrics(body.metrics)
            fields = story_text_fields(
                title=body.title,
                problem=body.problem,
                actions=body.actions,
                decisions=body.decisions,
                metrics=metrics,
                lessons=body.lessons,
                tags=body.tags,
            )
            _refuse_blocking(fields, blocklist, public_terms(pillars, blocklist, None))
            _refuse_sources(metrics, blocklist)
            story = StoryEntry(
                id=StoryEntry.new_id(now=now),
                created_at=stamp,
                updated_at=stamp,
                status=ReviewStatus.APPROVED,
                status_changed_at=stamp,
                origin="owner",
                title=body.title,
                problem=body.problem,
                actions=body.actions,
                decisions=body.decisions,
                metrics=metrics,
                lessons=body.lessons,
                pillars=body.pillars,
                confidentiality=body.confidentiality,
                tags=body.tags,
                approved_at=stamp,
            )
            check = await _classify_now(services, entry_text(story), stamp)
            if check is not None:
                story = story.model_copy(update={"classifier": check})
            stored = await create_doc(store, story)
        response.headers["Location"] = f"/api/stories/{stored.doc.id}"
        return versioned(stored, response)

    @router.get("/api/stories/{story_id}")
    async def get_story(
        story_id: str, store: StoreDep, response: Response
    ) -> Versioned[StoryEntry]:
        with problems():
            stored = await read_doc(store, StoryEntry, story_id)
        return versioned(stored, response)

    @router.put("/api/stories/{story_id}")
    async def put_story(
        story_id: str,
        body: StoryIn,
        services: ServicesDep,
        store: StoreDep,
        clock: ClockDep,
        if_match: IfMatch,
        response: Response,
    ) -> Versioned[StoryEntry]:
        with problems():
            now = clock.now()
            stamp = _stamp(now)
            blocklist = await _blocklist(store, stamp)
            current = await _current(store, StoryEntry, story_id, if_match)
            pillars = await _pillars(store, stamp)
            _check_pillars(body.pillars, pillars)
            metrics = _metrics(body.metrics)
            fields = story_text_fields(
                title=body.title,
                problem=body.problem,
                actions=body.actions,
                decisions=body.decisions,
                metrics=metrics,
                lessons=body.lessons,
                tags=body.tags,
            )
            _refuse_blocking(fields, blocklist, public_terms(pillars, blocklist, None))
            _refuse_sources(metrics, blocklist)
            edited = current.doc.model_copy(
                update={
                    "title": body.title,
                    "problem": body.problem,
                    "actions": body.actions,
                    "decisions": body.decisions,
                    "metrics": metrics,
                    "lessons": body.lessons,
                    "pillars": body.pillars,
                    "confidentiality": body.confidentiality,
                    "tags": body.tags,
                    "updated_at": stamp,
                }
            )
            check = None
            if not verdict_current(edited.classifier, entry_text(edited)):
                check = await _classify_now(services, entry_text(edited), stamp)
            stored = await replace_doc(store, _after_edit(edited, check), if_match=if_match)
        return versioned(stored, response)

    @router.delete("/api/stories/{story_id}", status_code=204, response_class=Response)
    async def delete_story(story_id: str, store: StoreDep, if_match: IfMatch) -> Response:
        with problems():
            await _current(store, StoryEntry, story_id, if_match)
            await delete_doc(store, StoryEntry, story_id, if_match=if_match)
        return Response(status_code=204)

    @router.post("/api/stories/{story_id}/review")
    async def review_story(
        story_id: str,
        body: ReviewIn,
        store: StoreDep,
        clock: ClockDep,
        if_match: IfMatch,
        response: Response,
    ) -> Versioned[StoryEntry]:
        with problems():
            current = await _current(store, StoryEntry, story_id, if_match)
            changed = _reviewed(current.doc, body.decision, _stamp(clock.now()))
            stored = await replace_doc(store, changed, if_match=if_match)
        return versioned(stored, response)

    # ---- POVs ----------------------------------------------------------------------------

    @router.get("/api/povs")
    async def list_povs(
        store: StoreDep,
        pillar: Annotated[str | None, Query(max_length=40)] = None,
        status: Annotated[ReviewStatus | None, Query()] = None,
        idea_id: Annotated[str | None, Query(alias="ideaId", max_length=255)] = None,
        limit: Annotated[int | None, Query()] = None,
        cursor: Annotated[str | None, Query(max_length=512)] = None,
    ) -> Page[PovEntry]:
        filters: list[Filter] = []
        if pillar is not None:
            filters.append(Filter("pillar", "==", pillar))
        if status is not None:
            filters.append(Filter("status", "==", status.value))
        if idea_id is not None:
            filters.append(Filter("ideaId", "==", idea_id))
        with problems():
            items, next_cursor = await _page_of(
                store, PovEntry, filters, limit=limit, cursor=cursor
            )
        return Page[PovEntry](items=[versioned(p) for p in items], next_cursor=next_cursor)

    @router.post("/api/povs", status_code=201)
    async def create_pov(
        body: PovIn, services: ServicesDep, store: StoreDep, clock: ClockDep, response: Response
    ) -> Versioned[PovEntry]:
        with problems():
            now = clock.now()
            stamp = _stamp(now)
            blocklist = await _blocklist(store, stamp)
            pillars = await _pillars(store, stamp)
            _check_pillars([body.pillar], pillars)
            await _check_idea(store, body.idea_id)
            fields = pov_text_fields(statement=body.statement, context=body.context)
            _refuse_blocking(fields, blocklist, public_terms(pillars, blocklist, body.pillar))
            pov = PovEntry(
                id=PovEntry.new_id(now=now),
                created_at=stamp,
                updated_at=stamp,
                status=ReviewStatus.APPROVED,
                status_changed_at=stamp,
                origin=body.origin,
                pillar=body.pillar,
                statement=body.statement,
                context=body.context,
                confidentiality=body.confidentiality,
                idea_id=body.idea_id,
                approved_at=stamp,
            )
            check = await _classify_now(services, entry_text(pov), stamp)
            if check is not None:
                pov = pov.model_copy(update={"classifier": check})
            stored = await create_doc(store, pov)
        response.headers["Location"] = f"/api/povs/{stored.doc.id}"
        return versioned(stored, response)

    @router.get("/api/povs/{pov_id}")
    async def get_pov(pov_id: str, store: StoreDep, response: Response) -> Versioned[PovEntry]:
        with problems():
            stored = await read_doc(store, PovEntry, pov_id)
        return versioned(stored, response)

    @router.put("/api/povs/{pov_id}")
    async def put_pov(
        pov_id: str,
        body: PovIn,
        services: ServicesDep,
        store: StoreDep,
        clock: ClockDep,
        if_match: IfMatch,
        response: Response,
    ) -> Versioned[PovEntry]:
        with problems():
            now = clock.now()
            stamp = _stamp(now)
            blocklist = await _blocklist(store, stamp)
            current = await _current(store, PovEntry, pov_id, if_match)
            pillars = await _pillars(store, stamp)
            _check_pillars([body.pillar], pillars)
            await _check_idea(store, body.idea_id)
            fields = pov_text_fields(statement=body.statement, context=body.context)
            _refuse_blocking(fields, blocklist, public_terms(pillars, blocklist, body.pillar))
            edited = current.doc.model_copy(
                update={
                    "pillar": body.pillar,
                    "statement": body.statement,
                    "context": body.context,
                    "confidentiality": body.confidentiality,
                    "idea_id": body.idea_id,
                    "updated_at": stamp,
                }
            )
            check = None
            if not verdict_current(edited.classifier, entry_text(edited)):
                check = await _classify_now(services, entry_text(edited), stamp)
            stored = await replace_doc(store, _after_edit(edited, check), if_match=if_match)
        return versioned(stored, response)

    @router.delete("/api/povs/{pov_id}", status_code=204, response_class=Response)
    async def delete_pov(pov_id: str, store: StoreDep, if_match: IfMatch) -> Response:
        with problems():
            await _current(store, PovEntry, pov_id, if_match)
            await delete_doc(store, PovEntry, pov_id, if_match=if_match)
        return Response(status_code=204)

    @router.post("/api/povs/{pov_id}/review")
    async def review_pov(
        pov_id: str,
        body: ReviewIn,
        store: StoreDep,
        clock: ClockDep,
        if_match: IfMatch,
        response: Response,
    ) -> Versioned[PovEntry]:
        with problems():
            current = await _current(store, PovEntry, pov_id, if_match)
            changed = _reviewed(current.doc, body.decision, _stamp(clock.now()))
            stored = await replace_doc(store, changed, if_match=if_match)
        return versioned(stored, response)

    return router


def _refuse_sources(metrics: Sequence[StoryMetric], blocklist: Blocklist) -> None:
    """Public sources of metrics are links by design: only the blocklist checks them."""
    sources = {
        f"metrics[{index}].publicSource": metric.public_source
        for index, metric in enumerate(metrics)
        if metric.public_source is not None
    }
    if sources:
        check_texts(sources, blocklist)


async def _check_idea(store: Store, idea_id: str | None) -> None:
    if idea_id is None:
        return
    try:
        await read_doc(store, Idea, idea_id)
    except NotFound:
        raise ProblemError(422, code="unknown_idea", detail="That idea does not exist.") from None
