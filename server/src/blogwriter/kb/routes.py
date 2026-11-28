"""HTTP routes of the knowledge base and policy settings (contract W-A2, ``/api/kb/*``).

Every ``GET`` returns ``Versioned[Doc]`` (``etag: null`` while the owner has not saved it: the
plan's defaults, :mod:`blogwriter.kb.defaults`) and never writes. Every ``PUT`` takes
``If-Match: <etag>`` or ``If-None-Match: *`` (first save) and answers 412 ``etag_mismatch`` with
the current etag when someone saved first, 422 when a value is out of range, 428 without a
precondition. A saved document is built from the current one (unknown fields kept) and
validated before anything is written.

Settings text that reaches a model, a search or a public render (pillars, format packs, series,
the brand kit, the policy) is screened against the blocklist once the owner has saved it
(422 ``confidential_text``, findings by field and term id). Blocklist terms never reach a log,
an event or a problem: kb logs counts, kinds and ids only.

``/api/kb/seeds`` reviews the v1 migration's keyword seeds (:mod:`blogwriter.kb.seeds`).
"""

from __future__ import annotations

import logging
import re
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Final

from fastapi import APIRouter, Response
from pydantic import ValidationError

from blogwriter.ai import AIRouter, AISettings, PriceTable
from blogwriter.ai.types import Task
from blogwriter.core.errors import JsonValue, ProblemError
from blogwriter.db import NotFound, PreconditionFailed, Store
from blogwriter.domain.api import Versioned
from blogwriter.domain.base import Document, SingletonDocument, to_iso
from blogwriter.domain.common import Finding, MediaRef, blocking
from blogwriter.domain.confidentiality import check_texts
from blogwriter.domain.deps import (
    ClockDep,
    ServicesDep,
    SingletonPrecondition,
    StoreDep,
    etag_mismatch,
    problems,
    versioned,
    versioned_singleton,
)
from blogwriter.domain.docstore import (
    InvalidDocumentWrite,
    Stored,
    read_doc,
    read_singleton,
    save_singleton,
    update_singleton,
)
from blogwriter.domain.ports import ModelRunner, Services, ServiceUnavailable
from blogwriter.domain.settings import (
    AutonomySettings,
    Blocklist,
    BrandColors,
    BrandKit,
    CadenceSettings,
    EmployerPolicy,
    FormatPacks,
    LaneSettings,
    ModelPreferences,
    PillarSettings,
    auto_publish_decision,
)
from blogwriter.jobs.clock import Clock
from blogwriter.kb.checks import (
    BRAND_CONTRAST_CODE,
    BRAND_MEDIA_CODE,
    Invalid,
    brand_texts,
    cadence_problems,
    format_texts,
    invalid,
    lane_problems,
    lane_texts,
    pillar_problems,
    pillar_texts,
    policy_texts,
    validate_brand,
    validation_error,
)
from blogwriter.kb.defaults import read_settings
from blogwriter.kb.inputs import OwnerInputs, owner_inputs
from blogwriter.kb.models import (
    AutonomyIn,
    AutonomyView,
    BlocklistIn,
    BlocklistTermIn,
    BrandIn,
    CadenceIn,
    DecisionView,
    FormatPacksIn,
    LanesIn,
    ModelPreferencesIn,
    ModelsView,
    PillarsIn,
    PolicyIn,
    PriceView,
    RouteModelView,
    RouteView,
)
from blogwriter.kb.seeds import (
    approve_seed,
    dismiss_seed,
    get_seed,
    list_seeds,
    restore_seed,
)
from blogwriter.kb.usage import pillar_references

__all__ = ["get_router"]

_logger: Final = logging.getLogger("blogwriter.kb")
_TERM_ID: Final = re.compile(r"t-([1-9][0-9]{0,8})")
_UPLOAD_ROLES: Final = frozenset({"upload", "image"})


# --------------------------------------------------------------------------------------------
# Shared steps of every save
# --------------------------------------------------------------------------------------------


def _stamp(clock: Clock) -> str:
    return to_iso(clock.now())


async def _base[S: SingletonDocument](
    store: Store, cls: type[S], precondition: str | None, *, now: str
) -> S:
    """The document a save starts from, after checking the precondition (412 otherwise)."""
    doc, etag = await read_settings(store, cls, now=now)
    if precondition is None:
        if etag is not None:  # If-None-Match: * but it was saved already
            raise etag_mismatch(Stored(doc, etag))
    elif etag != precondition:
        raise etag_mismatch(Stored(doc, etag) if etag is not None else None)
    return doc


def _build[S: SingletonDocument](current: S, updates: Mapping[str, object], *, now: str) -> S:
    """``current`` with ``updates`` (attribute names), validated (422 on the request body)."""
    candidate = current.model_copy(update={**updates, "updated_at": now})
    try:
        return type(current).model_validate(candidate.model_dump(warnings=False))
    except ValidationError as exc:
        raise validation_error(exc) from None


def _refuse(found: Sequence[Invalid]) -> None:
    if found:
        raise invalid(found)


async def _current_stored(store: Store, cls: type[SingletonDocument]) -> Stored[Document] | None:
    try:
        return await read_doc(store, cls, cls.SINGLETON_ID)
    except NotFound:
        return None


async def _save[S: SingletonDocument](store: Store, doc: S, precondition: str | None) -> Stored[S]:
    """Create (``If-None-Match: *``) or replace with If-Match; a lost race is a 412 that shows
    the current etag."""
    try:
        stored = await save_singleton(store, doc, if_match=precondition)
    except PreconditionFailed:
        raise etag_mismatch(await _current_stored(store, type(doc))) from None
    except InvalidDocumentWrite:
        raise invalid(
            [Invalid((), "These settings are too large to store.", "document_rules")]
        ) from None
    _logger.info("kb_settings_saved", extra={"kind": doc.KIND, "doc_id": doc.id})
    return stored


async def _screen(store: Store, texts: Mapping[str, str | None], *, now: str) -> None:
    """Refuse settings text that matches the blocklist (422 ``confidential_text``), once the
    owner has saved it. Until then the consumers of these texts fail closed themselves."""
    blocklist, _ = await read_singleton(store, Blocklist, now=now)
    if blocklist.configured:
        check_texts(texts, blocklist)


def _clean(value: str | None) -> str | None:
    """Trimmed text; blank means "not set"."""
    if value is None:
        return None
    text = value.strip()
    return text or None


def _clean_list(values: Sequence[str]) -> list[str]:
    """Trimmed, blanks and case-insensitive repeats dropped, order kept."""
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        text = value.strip()
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            out.append(text)
    return out


def _json_findings(findings: Sequence[Finding]) -> list[JsonValue]:
    return [finding.model_dump(mode="json", exclude_none=True) for finding in findings]


# --------------------------------------------------------------------------------------------
# Pillars, format packs, lanes
# --------------------------------------------------------------------------------------------


async def get_pillars(
    store: StoreDep, clock: ClockDep, response: Response
) -> Versioned[PillarSettings]:
    with problems():
        doc, etag = await read_settings(store, PillarSettings, now=_stamp(clock))
    return versioned_singleton(doc, etag, response)


async def put_pillars(
    body: PillarsIn,
    store: StoreDep,
    clock: ClockDep,
    precondition: SingletonPrecondition,
    response: Response,
) -> Versioned[PillarSettings]:
    now = _stamp(clock)
    with problems():
        current = await _base(store, PillarSettings, precondition, now=now)
        candidate = _build(current, {"pillars": body.pillars}, now=now)
        _refuse(pillar_problems(candidate))
        await _screen(store, pillar_texts(candidate), now=now)
        removed = {p.id for p in current.pillars} - {p.id for p in candidate.pillars}
        references = await pillar_references(store, among=removed) if removed else {}
        if references:
            used: dict[str, JsonValue] = {}
            for pillar, kinds in references.items():
                listed: list[JsonValue] = list(kinds)
                used[pillar] = listed
            pillars: list[JsonValue] = list(references)
            raise ProblemError(
                409,
                code="pillar_in_use",
                detail="A pillar you removed or renamed is still in use. Move what uses it first.",
                extensions={"pillars": pillars, "references": used},
            )
        stored = await _save(store, candidate, precondition)
    return versioned(stored, response)


async def get_formats(
    store: StoreDep, clock: ClockDep, response: Response
) -> Versioned[FormatPacks]:
    with problems():
        doc, etag = await read_settings(store, FormatPacks, now=_stamp(clock))
    return versioned_singleton(doc, etag, response)


async def put_formats(
    body: FormatPacksIn,
    store: StoreDep,
    clock: ClockDep,
    precondition: SingletonPrecondition,
    response: Response,
) -> Versioned[FormatPacks]:
    now = _stamp(clock)
    with problems():
        current = await _base(store, FormatPacks, precondition, now=now)
        candidate = _build(current, {"packs": body.packs}, now=now)
        await _screen(store, format_texts(candidate), now=now)
        stored = await _save(store, candidate, precondition)
    return versioned(stored, response)


async def get_lanes(
    store: StoreDep, clock: ClockDep, response: Response
) -> Versioned[LaneSettings]:
    with problems():
        doc, etag = await read_settings(store, LaneSettings, now=_stamp(clock))
    return versioned_singleton(doc, etag, response)


async def put_lanes(
    body: LanesIn,
    store: StoreDep,
    clock: ClockDep,
    precondition: SingletonPrecondition,
    response: Response,
) -> Versioned[LaneSettings]:
    now = _stamp(clock)
    with problems():
        current = await _base(store, LaneSettings, precondition, now=now)
        updates = {name: getattr(body, name) for name in LanesIn.model_fields}
        candidate = _build(current, updates, now=now)
        pillars, _ = await read_settings(store, PillarSettings, now=now)
        _refuse(lane_problems(candidate, {pillar.id for pillar in pillars.pillars}))
        await _screen(store, lane_texts(candidate), now=now)
        stored = await _save(store, candidate, precondition)
    return versioned(stored, response)


# --------------------------------------------------------------------------------------------
# Brand kit
# --------------------------------------------------------------------------------------------


def _declared_colors(colors: BrandColors) -> BrandColors:
    """Only the colour tokens the templates know: the renderer refuses any other key."""
    return BrandColors.model_validate(
        {name: getattr(colors, name) for name in BrandColors.model_fields}
    )


def _media(ref: MediaRef | None, role: str) -> MediaRef | None:
    """An upload used as the headshot or name mark carries that role."""
    if ref is not None and ref.role in _UPLOAD_ROLES:
        return ref.model_copy(update={"role": role})
    return ref


async def _check_uploads(services: Services, current: BrandKit, candidate: BrandKit) -> None:
    """New headshot or name-mark references must point at the uploaded bytes."""
    missing: list[Finding] = []
    for field, ref, before in (
        ("headshot", candidate.headshot, current.headshot),
        ("nameMark", candidate.name_mark, current.name_mark),
    ):
        if ref is None or (
            before is not None and (before.name, before.sha256) == (ref.name, ref.sha256)
        ):
            continue  # unchanged references were checked when they were saved
        blobs = services.blobs
        if blobs is None:
            raise ServiceUnavailable("storage")
        info = await blobs.head(ref.container, ref.name)
        if (
            info is None
            or info.sha256 != ref.sha256
            or info.size_bytes != ref.size_bytes
            or info.content_type != ref.content_type
        ):
            missing.append(
                Finding(
                    code=BRAND_MEDIA_CODE,
                    severity="block",
                    message="The uploaded image was not found. Upload it again.",
                    ref=field,
                )
            )
    if missing:
        raise ProblemError(
            422,
            code="brand_invalid",
            detail="Some brand kit values cannot be used. Fix the marked fields.",
            extensions={"findings": _json_findings(missing)},
        )


async def get_brand(store: StoreDep, clock: ClockDep, response: Response) -> Versioned[BrandKit]:
    with problems():
        doc, etag = await read_settings(store, BrandKit, now=_stamp(clock))
    return versioned_singleton(doc, etag, response)


async def put_brand(
    body: BrandIn,
    store: StoreDep,
    services: ServicesDep,
    clock: ClockDep,
    precondition: SingletonPrecondition,
    response: Response,
) -> Versioned[BrandKit]:
    now = _stamp(clock)
    with problems():
        current = await _base(store, BrandKit, precondition, now=now)
        briefs = {pillar: brief.strip() for pillar, brief in body.art_briefs.items()}
        updates: dict[str, object] = {
            "author_name": _clean(body.author_name),
            "author_headline": _clean(body.author_headline),
            "site_name": _clean(body.site_name),
            "colors": _declared_colors(body.colors),
            "font": body.font,
            "headshot": _media(body.headshot, "headshot"),
            "name_mark": _media(body.name_mark, "name_mark"),
            "art_briefs": {pillar: brief for pillar, brief in briefs.items() if brief},
        }
        candidate = _build(current, updates, now=now)
        pillars, _ = await read_settings(store, PillarSettings, now=now)
        if set(candidate.art_briefs) - {pillar.id for pillar in pillars.pillars}:
            raise invalid([Invalid(("artBriefs",), "Art briefs are keyed by your pillar ids.")])
        policy, _ = await read_singleton(store, EmployerPolicy, now=now)
        findings = validate_brand(candidate, disclaimer=policy.disclaimer_short)
        hard = [finding for finding in blocking(findings) if finding.ref != "disclaimerShort"]
        if hard:
            contrast = any(finding.code == BRAND_CONTRAST_CODE for finding in hard)
            raise ProblemError(
                422,
                code="brand_contrast" if contrast else "brand_invalid",
                detail=(
                    "Some colour pairs are below the WCAG AA contrast of 4.5:1."
                    if contrast
                    else "Some brand kit values cannot be used. Fix the marked fields."
                ),
                extensions={"findings": _json_findings(hard)},
            )
        await _check_uploads(services, current, candidate)
        await _screen(store, brand_texts(candidate), now=now)
        stored = await _save(store, candidate, precondition)
    return versioned(stored, response)


# --------------------------------------------------------------------------------------------
# Blocklist and employer policy
# --------------------------------------------------------------------------------------------


def _blocklist_terms(current: Blocklist, terms: Sequence[BlocklistTermIn]) -> list[object]:
    """Term bodies with ids: known ids are kept, new terms get ``t-<n>`` above every id in use.

    Bodies stay plain dicts so the whole list validates in one place (422 by position).
    """
    known = {term.id for term in current.terms}
    numbers = [
        int(match.group(1)) for term in current.terms if (match := _TERM_ID.fullmatch(term.id))
    ]
    next_number = max(numbers, default=0) + 1
    problems_found: list[Invalid] = []
    used: set[str] = set()
    folded: set[str] = set()
    bodies: list[object] = []
    for index, item in enumerate(terms):
        text = unicodedata.normalize("NFC", item.term).strip()
        if text.casefold() in folded:
            problems_found.append(Invalid(("terms", index, "term"), "Listed twice."))
        folded.add(text.casefold())
        term_id = item.id
        if term_id is None or term_id not in known or term_id in used:
            if term_id is not None and term_id in used:
                problems_found.append(Invalid(("terms", index, "id"), "Listed twice."))
            term_id = f"t-{next_number}"
            next_number += 1
        used.add(term_id)
        bodies.append({"id": term_id, "term": text, "category": item.category, "match": item.match})
    _refuse(problems_found)
    return bodies


async def get_blocklist(
    store: StoreDep, clock: ClockDep, response: Response
) -> Versioned[Blocklist]:
    with problems():
        doc, etag = await read_settings(store, Blocklist, now=_stamp(clock))
    return versioned_singleton(doc, etag, response)


async def put_blocklist(
    body: BlocklistIn,
    store: StoreDep,
    clock: ClockDep,
    precondition: SingletonPrecondition,
    response: Response,
) -> Versioned[Blocklist]:
    now = _stamp(clock)
    with problems():
        current = await _base(store, Blocklist, precondition, now=now)
        terms = _blocklist_terms(current, body.terms)
        candidate = _build(current, {"terms": terms, "configured": True}, now=now)
        stored = await _save(store, candidate, precondition)
    _logger.info("kb_blocklist_saved", extra={"count": len(stored.doc.terms)})
    return versioned(stored, response)


async def get_policy(
    store: StoreDep, clock: ClockDep, response: Response
) -> Versioned[EmployerPolicy]:
    with problems():
        doc, etag = await read_settings(store, EmployerPolicy, now=_stamp(clock))
    return versioned_singleton(doc, etag, response)


async def put_policy(
    body: PolicyIn,
    store: StoreDep,
    clock: ClockDep,
    precondition: SingletonPrecondition,
    response: Response,
) -> Versioned[EmployerPolicy]:
    now = _stamp(clock)
    with problems():
        current = await _base(store, EmployerPolicy, precondition, now=now)
        confirmed_at: str | None = None
        if body.ai_use_confirmed:  # stamped when it turns true, kept while it stays true
            previous = current.ai_use_confirmed_at if current.ai_use_confirmed else None
            confirmed_at = previous or now
        updates: dict[str, object] = {
            "disclaimer_short": _clean(body.disclaimer_short),
            "disclaimer_long": _clean(body.disclaimer_long),
            "forbidden_topics": _clean_list(body.forbidden_topics),
            "no_employer_branding": body.no_employer_branding,
            "ai_use_confirmed": body.ai_use_confirmed,
            "ai_use_confirmed_at": confirmed_at,
            "clearance_statement": body.clearance_statement.strip(),
            "configured": True,
        }
        candidate = _build(current, updates, now=now)
        brand, _ = await read_settings(store, BrandKit, now=now)
        if any(
            finding.ref == "disclaimerShort"
            for finding in blocking(validate_brand(brand, disclaimer=candidate.disclaimer_short))
        ):
            raise invalid(
                [Invalid(("disclaimerShort",), "Has characters that cannot be shown on slides.")]
            )
        await _screen(store, policy_texts(candidate), now=now)
        stored = await _save(store, candidate, precondition)
    return versioned(stored, response)


# --------------------------------------------------------------------------------------------
# Cadence
# --------------------------------------------------------------------------------------------


async def _reseed(services: Services) -> None:
    """Re-put the schedule items for the new cadence; a failure is logged, never fatal (the
    seeder retries it in the background until it succeeds)."""
    seeder = services.schedules
    if seeder is None:
        _logger.warning("kb_reseed_skipped", extra={"reason": "schedules_unavailable"})
        return
    try:
        await seeder.reseed()
    except Exception as exc:
        _logger.warning("kb_reseed_failed", extra={"error_type": type(exc).__name__})


async def get_cadence(
    store: StoreDep, clock: ClockDep, response: Response
) -> Versioned[CadenceSettings]:
    with problems():
        doc, etag = await read_settings(store, CadenceSettings, now=_stamp(clock))
    return versioned_singleton(doc, etag, response)


async def put_cadence(
    body: CadenceIn,
    store: StoreDep,
    services: ServicesDep,
    clock: ClockDep,
    precondition: SingletonPrecondition,
    response: Response,
) -> Versioned[CadenceSettings]:
    now = _stamp(clock)
    with problems():
        current = await _base(store, CadenceSettings, precondition, now=now)
        updates = {name: getattr(body, name) for name in CadenceIn.model_fields}
        candidate = _build(current, updates, now=now)
        _refuse(cadence_problems(candidate))
        stored = await _save(store, candidate, precondition)
    await _reseed(services)
    return versioned(stored, response)


# --------------------------------------------------------------------------------------------
# Models (routing and prices are read-only: the running router's, configured by BW_AI_*)
# --------------------------------------------------------------------------------------------


def _route_views(runner: ModelRunner | None) -> list[RouteView]:
    if not isinstance(runner, AIRouter):
        return []
    table = runner.routing
    views: list[RouteView] = []
    for task in Task:
        route = table.get(task)
        if route is None:
            continue
        views.append(
            RouteView(
                task=task.value,
                max_class=route.policy.max_sensitivity.value,
                tools=route.policy.tools,
                chain=[
                    RouteModelView(
                        model=spec.family,
                        deployment=spec.deployment,
                        provider=spec.provider,
                        effort=spec.effort,
                    )
                    for spec in route.chain
                ],
            )
        )
    return views


def _price_table(runner: ModelRunner | None) -> PriceTable | None:
    """The running router's prices (what every call is costed with: ``AIRouter.prices``, I-11);
    without a router, the ones ``BW_AI_*`` configures (``None`` when they are unreadable)."""
    if isinstance(runner, AIRouter):
        return runner.prices
    try:
        return AISettings().price_table()
    except ValueError as exc:  # a bad BW_AI_* value (pydantic's ValidationError is a ValueError)
        _logger.warning("kb_prices_unavailable", extra={"error_type": type(exc).__name__})
        return None


def _price_views(runner: ModelRunner | None) -> tuple[list[PriceView], float | None]:
    table = _price_table(runner)
    if table is None:
        return [], None
    prices = [
        PriceView(
            model=model,
            input=float(price.input),
            output=float(price.output),
            cached_input=None if price.cached_input is None else float(price.cached_input),
            cache_write=None if price.cache_write is None else float(price.cache_write),
            image_usd={size: float(usd) for size, usd in price.image_sizes.items()} or None,
        )
        for model, price in sorted(table.models.items())
    ]
    return prices, float(table.web_search_per_1k)


def _routes_images(runner: ModelRunner | None) -> bool:
    return isinstance(runner, AIRouter) and Task.IMAGE in runner.routing


async def get_models(
    store: StoreDep, services: ServicesDep, clock: ClockDep, response: Response
) -> ModelsView:
    with problems():
        preferences, etag = await read_settings(store, ModelPreferences, now=_stamp(clock))
    prices, web_search = _price_views(services.ai)
    return ModelsView(
        preferences=versioned_singleton(preferences, etag, response),
        routes=_route_views(services.ai),
        prices=prices,
        web_search_usd_per_1k=web_search,
    )


async def put_models(
    body: ModelPreferencesIn,
    store: StoreDep,
    services: ServicesDep,
    clock: ClockDep,
    precondition: SingletonPrecondition,
    response: Response,
) -> Versioned[ModelPreferences]:
    now = _stamp(clock)
    with problems():
        current = await _base(store, ModelPreferences, precondition, now=now)
        if body.images_enabled and not current.images_enabled and not _routes_images(services.ai):
            raise ProblemError(
                409,
                code="images_unavailable",
                detail="Illustrations need an image model route, and none is configured.",
            )
        updates = {name: getattr(body, name) for name in ModelPreferencesIn.model_fields}
        candidate = _build(current, updates, now=now)
        stored = await _save(store, candidate, precondition)
    return versioned(stored, response)


# --------------------------------------------------------------------------------------------
# Autonomy
# --------------------------------------------------------------------------------------------


def _autonomy_view(
    doc: AutonomySettings, etag: str | None, now: datetime, response: Response
) -> AutonomyView:
    decision = auto_publish_decision(doc, now=now)
    return AutonomyView(
        settings=versioned_singleton(doc, etag, response),
        decision=DecisionView(allowed=decision.allowed, reasons=list(decision.reasons)),
    )


async def get_autonomy(store: StoreDep, clock: ClockDep, response: Response) -> AutonomyView:
    moment = clock.now()
    with problems():
        doc, etag = await read_settings(store, AutonomySettings, now=to_iso(moment))
    return _autonomy_view(doc, etag, moment, response)


async def put_autonomy(
    body: AutonomyIn,
    store: StoreDep,
    clock: ClockDep,
    precondition: SingletonPrecondition,
    response: Response,
) -> AutonomyView:
    moment = clock.now()
    now = to_iso(moment)
    with problems():
        current = await _base(store, AutonomySettings, precondition, now=now)
        candidate = _build(current, {"auto_publish_enabled": body.auto_publish_enabled}, now=now)
        stored = await _save(store, candidate, precondition)
    return _autonomy_view(stored.doc, stored.etag, moment, response)


async def _set_paused(
    store: Store, clock: Clock, response: Response, *, paused: bool
) -> AutonomyView:
    """Pause or resume all autonomy: idempotent, no If-Match (a one-tap phone action)."""
    moment = clock.now()
    now = to_iso(moment)
    changed = False

    def mutate(doc: AutonomySettings) -> AutonomySettings | None:
        nonlocal changed
        if doc.paused is paused:
            return None
        changed = True
        return doc.model_copy(
            update={"paused": paused, "paused_at": now if paused else None, "updated_at": now}
        )

    with problems():
        doc, etag = await update_singleton(store, AutonomySettings, mutate, now=now)
    if changed:
        event = "kb_autonomy_paused" if paused else "kb_autonomy_resumed"
        _logger.info(event, extra={"kind": AutonomySettings.KIND})
    return _autonomy_view(doc, etag, moment, response)


async def pause_autonomy(store: StoreDep, clock: ClockDep, response: Response) -> AutonomyView:
    return await _set_paused(store, clock, response, paused=True)


async def resume_autonomy(store: StoreDep, clock: ClockDep, response: Response) -> AutonomyView:
    return await _set_paused(store, clock, response, paused=False)


# --------------------------------------------------------------------------------------------
# Owner inputs and the router
# --------------------------------------------------------------------------------------------


async def get_inputs(store: StoreDep, clock: ClockDep) -> OwnerInputs:
    with problems():
        return await owner_inputs(store, now=clock.now())


type _Endpoint = Callable[..., object]

_ROUTES: Final[tuple[tuple[str, str, _Endpoint, str], ...]] = (
    ("GET", "/pillars", get_pillars, "The content pillars"),
    ("PUT", "/pillars", put_pillars, "Save the content pillars"),
    ("GET", "/formats", get_formats, "The article format packs"),
    ("PUT", "/formats", put_formats, "Save the article format packs"),
    ("GET", "/lanes", get_lanes, "The post lanes and weekly mix rules"),
    ("PUT", "/lanes", put_lanes, "Save the post lanes and weekly mix rules"),
    ("GET", "/brand", get_brand, "The brand kit"),
    ("PUT", "/brand", put_brand, "Save the brand kit"),
    ("GET", "/blocklist", get_blocklist, "The confidentiality blocklist"),
    ("PUT", "/blocklist", put_blocklist, "Save the confidentiality blocklist"),
    ("GET", "/policy", get_policy, "The employer policy"),
    ("PUT", "/policy", put_policy, "Save the employer policy"),
    ("GET", "/cadence", get_cadence, "The weekly cadence, slots and modes"),
    ("PUT", "/cadence", put_cadence, "Save the cadence and reseed the schedules"),
    ("GET", "/models", get_models, "Model routing, prices and preferences"),
    ("PUT", "/models", put_models, "Save the model preferences"),
    ("GET", "/autonomy", get_autonomy, "Autonomy switches and the auto-publish decision"),
    ("PUT", "/autonomy", put_autonomy, "Turn auto-publish on or off"),
    ("POST", "/autonomy/pause", pause_autonomy, "Pause all autonomy"),
    ("POST", "/autonomy/resume", resume_autonomy, "Resume autonomy"),
    ("GET", "/inputs", get_inputs, "The owner-input checklist"),
    ("GET", "/seeds", list_seeds, "The v1 migration's keyword seeds"),
    ("GET", "/seeds/{seed_id}", get_seed, "One keyword seed"),
    ("POST", "/seeds/{seed_id}/approve", approve_seed, "Add a seed's keywords to a pillar"),
    ("POST", "/seeds/{seed_id}/dismiss", dismiss_seed, "Dismiss a keyword seed"),
    ("POST", "/seeds/{seed_id}/restore", restore_seed, "Put a dismissed seed back in review"),
)


def get_router() -> APIRouter:
    """``/api/kb/*`` (owner only; every non-GET passes CSRF in core)."""
    router = APIRouter(prefix="/api/kb", tags=["kb"])
    for method, path, endpoint, summary in _ROUTES:
        router.add_api_route(path, endpoint, methods=[method], summary=summary)
    return router
