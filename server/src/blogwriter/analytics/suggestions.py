"""Insight suggestions: the weekly ``analytics.suggest`` job and the owner's decision.

**Suggestions only.** The job writes ``insight_suggestion`` documents and repurpose ideas in the
ideas inbox, nothing else: no plan, setting, post or article changes, and accepting a suggestion
records the decision only (the planner never self-adjusts; the owner makes any change by hand).

``analytics.suggest`` (schedule Mon 07:30, or the owner's "run now"):

``repurpose``
    Repurposing (plan: "Post lanes"), through signals' ``propose_repurpose``: posts that beat
    the owner's median engagement rate in the window become flagship candidates, and evergreen
    posts (not time-sensitive, not quick takes) that went out ``LaneSettings.repurpose``
    8-12 weeks ago get a re-cut idea. At most :data:`REPURPOSE_PER_RUN` new ideas of each kind;
    ideas have derived ids, so a post is proposed once per kind. No model call. Skipped while
    autonomy is paused (background runs).
``compute``
    Background runs finish with ``{"skipped": "paused"}`` while autonomy is paused. Rates per
    1K impressions vs the owner's trailing median per dimension over the last 12 weeks
    (:mod:`blogwriter.analytics.rates`; numbers and dimension values only) and the week
    (``weekStart``: the Monday of the run, cadence time zone). Fewer than :data:`MIN_POSTS`
    measured posts: ``{"skipped": "not_enough_data"}``.
``suggest``
    When the week already has suggestions, those are the result (a resumed or repeated run
    never asks again). Otherwise the dimension values sent clear the blocklist, then SYNTHESIS
    (``Sensitivity.PUBLIC``: aggregates only, never post text) runs inside ``defer_on_brake``,
    and the kept suggestions are saved right away with ``InsightSuggestion.id_for(weekStart,
    dimension, n)`` (``Conflict`` = saved). The model's text is written to the documents, never
    to the job (outputs carry ids and counts).
``save``
    Emits ``entity.changed`` for each suggestion and returns the ids.

A suggestion is kept only when its dimension and value are lines that were sent, its text fits
(500 characters) and carries no AI tell or formula opener; at most 2 per dimension and 6 in all.
Its ``evidence`` is written by code from the numbers, never by the model.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final, Literal

import regex
from pydantic import Field, JsonValue, ValidationError

from blogwriter.ai.types import Message, Request, Sensitivity, Task
from blogwriter.analytics._prompts import Prompt, load_prompt
from blogwriter.analytics._support import (
    MAX_ENTITY_EVENTS,
    as_object,
    as_string,
    as_strings,
    job_origin,
)
from blogwriter.analytics.rates import (
    ENGAGEMENT,
    RATE_KEYS,
    InsightDimension,
    InsightsOverview,
    InsightsRow,
    MetricRates,
    build_overview,
    load_post_metrics,
    median_rates,
)
from blogwriter.db import Conflict, Filter, NotFound, OrderBy, Store
from blogwriter.domain.analytics import Experiment, InsightSuggestion
from blogwriter.domain.api import ApiModel
from blogwriter.domain.base import distinct_stamps, parse_iso, to_iso
from blogwriter.domain.common import ServedByRecord
from blogwriter.domain.confidentiality import (
    BlocklistNotConfigured,
    ConfidentialText,
    check_texts,
)
from blogwriter.domain.docstore import (
    InvalidStoredDocument,
    create_doc,
    find_docs,
    read_doc,
    read_singleton,
)
from blogwriter.domain.jobflow import defer_on_brake, require_service, step_services
from blogwriter.domain.jobtypes import ENTITY_CHANGED, entity_changed
from blogwriter.domain.posts import Post
from blogwriter.domain.settings import (
    AutonomySettings,
    Blocklist,
    CadenceSettings,
    LaneSettings,
    RepurposeRules,
)
from blogwriter.domain.states import (
    SUGGESTION_MACHINE,
    ExperimentStatus,
    PostStatus,
    SuggestionStatus,
)
from blogwriter.domain.weeks import local_date_time, week_start
from blogwriter.jobs import JobFailed, Step, StepContext
from blogwriter.linkedin.lint import AI_TELLS
from blogwriter.signals import RepurposeKind, propose_repurpose

__all__ = [
    "MAX_PER_DIMENSION",
    "MAX_SUGGESTIONS",
    "MIN_GROUP_POSTS",
    "MIN_POSTS",
    "SUGGEST_STEPS",
    "VALUE_FIELD",
    "WINDOW_WEEKS",
    "ChosenSuggestion",
    "InsightDecisionIn",
    "InsightDraft",
    "InsightsDraft",
    "chosen_suggestions",
    "decided",
    "eligible_rows",
    "evidence_for",
    "has_tell",
    "save_suggestions",
    "suggest_compute",
    "suggest_generate",
    "suggest_job_id",
    "suggest_repurpose",
    "suggest_save",
    "suggestion_request",
]

MIN_POSTS: Final = 6
"""Measured posts in the window before the job suggests anything."""
MIN_GROUP_POSTS: Final = 2
"""Lines with fewer posts are not sent to the model."""
MAX_SUGGESTIONS: Final = 6
MAX_PER_DIMENSION: Final = 2
MAX_TEXT_CHARS: Final = 500
MAX_EVIDENCE_CHARS: Final = 1000
VALUE_FIELD: Final = "value"
"""The ``InsightSuggestion.value`` key: the dimension value a suggestion is about."""
WINDOW_WEEKS: Final = 12
REPURPOSE_PER_RUN: Final = 3
"""New repurpose ideas of each kind one run proposes at most (the inbox stays short)."""
REPURPOSE_CANDIDATES: Final = 100

_BLOCKLIST_DETAIL: Final = "Save the blocklist once in Settings > Policy first."
_APOSTROPHES: Final = str.maketrans({"\u2019": "'", "\u2018": "'"})
_OPENERS: Final = (
    r"unpopular\s+opinion\b",
    r"here(?:'s|\s+is)\s+the\s+thing\b",
    r"nobody\s+(?:is\s+)?talk(?:s|ing)\s+about\b",
    r"most\s+people\s+get\s+.{1,60}?\s+wrong\b",
    r"[\w .#+-]{1,40}?\s+is\s+dead(?=\s*(?:[.!:,]|$))",
    r"let\s+that\s+sink\s+in\b",
)
_TELLS: Final = (
    *(
        regex.compile(rf"(?i)(?<![\p{{L}}\p{{N}}]){pattern}(?![\p{{L}}\p{{N}}])")
        for pattern in AI_TELLS
    ),
    *(regex.compile(rf"(?i)^[\s\W]{{0,4}}{pattern}") for pattern in _OPENERS),
    regex.compile(r"(?i)(?<![\p{L}\p{N}])let\s+that\s+sink\s+in\b"),
)


def suggest_job_id(now: datetime) -> str:
    """The owner's suggestion run of the day (``enqueue_once`` with ``rerun``)."""
    return f"analytics-suggest-run-{now:%Y%m%d}"


# --------------------------------------------------------------------------------------------
# The model's output and the request
# --------------------------------------------------------------------------------------------


class InsightDraft(ApiModel):
    dimension: InsightDimension = Field(
        description="The dimension of the line the suggestion rests on."
    )
    value: str = Field(description="The value of that line, copied exactly from <data>.")
    text: str = Field(description="One or two plain sentences, at most 300 characters.")


class InsightsDraft(ApiModel):
    """The SYNTHESIS output (lengths and counts are enforced in code, not by the schema)."""

    suggestions: list[InsightDraft]


class InsightDecisionIn(ApiModel):
    decision: Literal["accept", "dismiss"]


def eligible_rows(overview: InsightsOverview) -> list[InsightsRow]:
    """The lines sent to the model: at least :data:`MIN_GROUP_POSTS` posts."""
    return [row for row in overview.rows if row.posts >= MIN_GROUP_POSTS]


def _rate(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def _percent(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:+.0f}%"


def _rates_line(rates: MetricRates) -> str:
    return "; ".join(f"{key} {_rate(getattr(rates, key))}" for key in RATE_KEYS)


def suggestion_request(
    overview: InsightsOverview,
    *,
    experiment: str | None,
    prompt: Prompt,
) -> Request:
    """The SYNTHESIS request: public aggregates (rates, medians, counts, dimension values)."""
    lines = [
        "<data>",
        f'<window weeks="{overview.weeks}" posts="{overview.posts}"/>',
        f"<owner_median>{_rates_line(overview.owner_median)}</owner_median>",
        "<lines>",
    ]
    for row in eligible_rows(overview):
        cells = "; ".join(
            f"{key} {_rate(getattr(row.rates_per_1k, key))} "
            f"({_percent(getattr(row.vs_median, key))})"
            for key in RATE_KEYS
        )
        lines.append(f"dimension={row.dimension} value={row.value} posts={row.posts} | {cells}")
    lines.append("</lines>")
    if experiment is not None:
        lines.append(f'<experiment question="{experiment}"/>')
    lines.append("</data>")
    lines.append("Write the suggestions. Reply with the JSON object only.")
    return Request(
        system=prompt.text,
        messages=(Message.user("\n".join(lines)),),
        sensitivity=Sensitivity.PUBLIC,
        output_schema=InsightsDraft,
    )


# --------------------------------------------------------------------------------------------
# Keeping and saving suggestions
# --------------------------------------------------------------------------------------------


def has_tell(text: str) -> bool:
    """Whether ``text`` has an AI tell (``linkedin.lint.AI_TELLS``) or a formula opener."""
    normalized = text.translate(_APOSTROPHES)
    return any(pattern.search(normalized) for pattern in _TELLS)


def evidence_for(row: InsightsRow, owner: MetricRates) -> str:
    """The numbers behind a suggestion (written by code, never by the model)."""
    parts = [f"{row.dimension}={row.value}, {row.posts} posts"]
    for key in RATE_KEYS:
        value: float | None = getattr(row.rates_per_1k, key)
        if value is None:
            continue
        piece = f"{key} {value:.2f}/1K"
        base: float | None = getattr(owner, key)
        if base is not None:
            piece += f" vs median {base:.2f}"
        relative: float | None = getattr(row.vs_median, key)
        if relative is not None:
            piece += f" ({relative * 100:+.0f}%)"
        parts.append(piece)
    return "; ".join(parts)[:MAX_EVIDENCE_CHARS]


@dataclass(frozen=True, slots=True)
class ChosenSuggestion:
    dimension: InsightDimension
    value: str
    """The dimension value (the line) the suggestion rests on."""
    ordinal: int
    """Position among this week's suggestions of the same dimension (the id's ``n``)."""
    text: str
    evidence: str


def chosen_suggestions(draft: InsightsDraft, overview: InsightsOverview) -> list[ChosenSuggestion]:
    """The suggestions worth keeping (module docs), in the model's order."""
    lines = {(row.dimension, row.value): row for row in eligible_rows(overview)}
    per_dimension: dict[str, int] = {}
    kept: list[ChosenSuggestion] = []
    for item in draft.suggestions:
        if len(kept) >= MAX_SUGGESTIONS:
            break
        text = " ".join(item.text.split())
        row = lines.get((item.dimension, item.value.strip()))
        if row is None or not text or len(text) > MAX_TEXT_CHARS or has_tell(text):
            continue
        ordinal = per_dimension.get(item.dimension, 0)
        if ordinal >= MAX_PER_DIMENSION:
            continue
        per_dimension[item.dimension] = ordinal + 1
        kept.append(
            ChosenSuggestion(
                dimension=item.dimension,
                value=row.value,
                ordinal=ordinal,
                text=text,
                evidence=evidence_for(row, overview.owner_median),
            )
        )
    return kept


async def save_suggestions(
    store: Store,
    week: str,
    chosen: Sequence[ChosenSuggestion],
    *,
    served_by: ServedByRecord | None,
    now: datetime,
) -> list[str]:
    """Create the week's suggestions (deterministic ids; ``Conflict`` = saved earlier), each
    with the dimension value it is about (``value``)."""
    ids: list[str] = []
    for item, stamp in zip(chosen, distinct_stamps(now, len(chosen)), strict=True):
        suggestion = InsightSuggestion.model_validate(
            {
                "id": InsightSuggestion.id_for(week, item.dimension, item.ordinal),
                "createdAt": stamp,
                "updatedAt": stamp,
                "status": SuggestionStatus.PROPOSED,
                "statusChangedAt": stamp,
                "weekStart": week,
                "dimension": item.dimension,
                VALUE_FIELD: item.value,
                "text": item.text,
                "evidence": item.evidence,
                "servedBy": served_by,
            }
        )
        try:
            stored = await create_doc(store, suggestion)
        except Conflict:
            stored = await read_doc(store, InsightSuggestion, suggestion.id)
        ids.append(stored.doc.id)
    return ids


def decided(
    current: InsightSuggestion, decision: Literal["accept", "dismiss"], *, now: datetime
) -> InsightSuggestion:
    """The suggestion after the owner's decision (``SUGGESTION_MACHINE``; records only)."""
    stamp = to_iso(now)
    wanted = SuggestionStatus.ACCEPTED if decision == "accept" else SuggestionStatus.DISMISSED
    target = SUGGESTION_MACHINE.transition(current.status, wanted)
    return current.model_copy(
        update={
            "status": target,
            "status_changed_at": stamp,
            "decided_at": stamp,
            "updated_at": stamp,
        }
    )


# --------------------------------------------------------------------------------------------
# Steps
# --------------------------------------------------------------------------------------------


async def suggest_compute(ctx: StepContext) -> JsonValue:
    """Rates vs the trailing median per dimension (or a skip)."""
    services = step_services()
    store = services.store
    now = services.clock.now()
    stamp = to_iso(now)
    if job_origin(ctx) == "background":
        autonomy, _ = await read_singleton(store, AutonomySettings, now=stamp)
        if autonomy.paused:
            return {"skipped": "paused"}
    cadence, _ = await read_singleton(store, CadenceSettings, now=stamp)
    run_at = to_iso(parse_iso(ctx.scheduled_for)) if ctx.scheduled_for else stamp
    week = week_start(local_date_time(run_at, cadence.timezone)[0])
    overview = await build_overview(store, now=now, weeks=WINDOW_WEEKS)
    if overview.posts < MIN_POSTS:
        return {"skipped": "not_enough_data", "weekStart": week, "posts": overview.posts}
    return {"weekStart": week, "overview": overview.model_dump(mode="json", by_alias=True)}


async def _week_suggestions(store: Store, week: str) -> list[str]:
    found = await find_docs(
        store, InsightSuggestion, [Filter("weekStart", "==", week)], strict=True
    )
    ordered = sorted(found, key=lambda stored: (stored.doc.created_at, stored.doc.id))
    return [stored.doc.id for stored in ordered]


async def _running_question(store: Store) -> str | None:
    running = await find_docs(
        store, Experiment, [Filter("status", "==", ExperimentStatus.RUNNING.value)], limit=1
    )
    return running[0].doc.question if running else None


def _check_values(rows: Sequence[InsightsRow], blocklist: Blocklist) -> None:
    """Owner-chosen values (pillar ids) bound for the model clear the blocklist."""
    try:
        check_texts({f"lines[{index}]": row.value for index, row in enumerate(rows)}, blocklist)
    except BlocklistNotConfigured:
        raise JobFailed("blocklist_not_configured", _BLOCKLIST_DETAIL) from None
    except ConfidentialText:
        raise JobFailed(
            "blocklist_hit", "A pillar id matches the blocklist. Rename it, then run again."
        ) from None


async def suggest_generate(ctx: StepContext) -> JsonValue:
    """SYNTHESIS over the aggregates; keeps and saves the suggestions (module docs)."""
    computed = as_object(ctx.outputs.get("compute"))
    if "skipped" in computed:
        return computed
    week = as_string(computed.get("weekStart"))
    try:
        overview = InsightsOverview.model_validate(computed.get("overview"))
    except ValidationError:
        raise JobFailed("invalid_step_output", "The compute step left no numbers.") from None
    if week is None:
        raise JobFailed("invalid_step_output", "The compute step left no week.")
    services = step_services()
    store = services.store
    now = services.clock.now()
    stamp = to_iso(now)
    existing = await _week_suggestions(store, week)
    if existing:
        return {"weekStart": week, "suggestionIds": list[JsonValue](existing), "existing": True}
    rows = eligible_rows(overview)
    if not rows:
        return {"weekStart": week, "suggestionIds": [], "proposed": 0}
    blocklist, _ = await read_singleton(store, Blocklist, now=stamp)
    _check_values(rows, blocklist)
    ai = require_service(services.ai, "ai")
    prompt = load_prompt("suggest")
    request = suggestion_request(overview, experiment=await _running_question(store), prompt=prompt)
    with defer_on_brake(now):
        result = await ai.run(Task.SYNTHESIS, request, origin=job_origin(ctx), job_id=ctx.job_id)
    if result.refused:
        raise JobFailed("synthesis_refused", "The model declined to write suggestions. Run again.")
    try:
        draft = result.output(InsightsDraft)
    except TypeError:
        raise JobFailed(
            "synthesis_empty", "The model returned no suggestions. Run again."
        ) from None
    record = ServedByRecord.from_served_by(
        result.served_by,
        step="suggest",
        at=stamp,
        refused=bool(result.refusals),
        cost_usd=float(result.cost_usd),
        prompt_version=prompt.version,
    )
    chosen = chosen_suggestions(draft, overview)
    ids = await save_suggestions(store, week, chosen, served_by=record, now=now)
    return {
        "weekStart": week,
        "suggestionIds": list[JsonValue](ids),
        "proposed": len(draft.suggestions),
    }


async def suggest_save(ctx: StepContext) -> JsonValue:
    """Tell the web about the week's suggestions and finish with their ids."""
    generated = as_object(ctx.outputs.get("suggest"))
    if "skipped" in generated:
        return generated
    ids = as_strings(generated.get("suggestionIds"))
    for suggestion_id in ids[:MAX_ENTITY_EVENTS]:
        await ctx.emit(ENTITY_CHANGED, entity_changed("insight_suggestion", suggestion_id))
    return {
        "weekStart": generated.get("weekStart"),
        "suggestionIds": list[JsonValue](ids),
        "count": len(ids),
    }


async def _propose(
    store: Store, posts: Sequence[Post], kind: RepurposeKind, *, now: datetime
) -> list[str]:
    created: list[str] = []
    for post in posts:
        if len(created) == REPURPOSE_PER_RUN:
            break
        outcome = await propose_repurpose(store, post, kind=kind, now=now)
        if outcome.created:
            created.append(outcome.stored.doc.id)
    return created


async def _beat_median(store: Store, *, now: datetime) -> list[Post]:
    """Posts that went out in the window with an engagement rate above the owner's median
    (best first; none while fewer than :data:`MIN_POSTS` were measured)."""
    since = to_iso(now - timedelta(weeks=WINDOW_WEEKS))
    metrics = await load_post_metrics(store, since=since, until=to_iso(now))
    if len(metrics) < MIN_POSTS:
        return []
    median = median_rates(metrics).get(ENGAGEMENT)
    if median is None or median <= 0:
        return []
    above = [item for item in metrics if (item.rates.get(ENGAGEMENT) or 0.0) > median]
    above.sort(key=lambda item: -(item.rates.get(ENGAGEMENT) or 0.0))
    posts: list[Post] = []
    for item in above:
        try:
            posts.append((await read_doc(store, Post, item.post_id)).doc)
        except (NotFound, InvalidStoredDocument):
            continue
    return posts


async def _evergreen(store: Store, rules: RepurposeRules, *, now: datetime) -> list[Post]:
    """Posts that went out ``min_weeks``-``max_weeks`` ago and are not time-bound (oldest
    first)."""
    found = await find_docs(
        store,
        Post,
        [
            Filter("status", "==", PostStatus.POSTED.value),
            Filter("postedAt", ">=", to_iso(now - timedelta(weeks=rules.max_weeks))),
            Filter("postedAt", "<=", to_iso(now - timedelta(weeks=rules.min_weeks))),
        ],
        order_by=OrderBy("postedAt"),
        limit=REPURPOSE_CANDIDATES,
    )
    return [
        stored.doc
        for stored in found
        if not stored.doc.time_sensitive and stored.doc.lane != "quick_take"
    ]


async def suggest_repurpose(ctx: StepContext) -> JsonValue:
    """Repurpose ideas: flagship candidates and evergreen re-cuts (module docs)."""
    services = step_services()
    store = services.store
    now = services.clock.now()
    stamp = to_iso(now)
    if job_origin(ctx) == "background":
        autonomy, _ = await read_singleton(store, AutonomySettings, now=stamp)
        if autonomy.paused:
            return {"skipped": "paused"}
    lanes, _ = await read_singleton(store, LaneSettings, now=stamp)
    flagship = await _propose(store, await _beat_median(store, now=now), "flagship", now=now)
    recut = await _propose(
        store, await _evergreen(store, lanes.repurpose, now=now), "recut", now=now
    )
    for idea_id in [*flagship, *recut][:MAX_ENTITY_EVENTS]:
        await ctx.emit(ENTITY_CHANGED, entity_changed("idea", idea_id))
    return {
        "flagship": list[JsonValue](flagship),
        "recut": list[JsonValue](recut),
    }


SUGGEST_STEPS: Final = (
    Step("repurpose", suggest_repurpose),
    Step("compute", suggest_compute),
    Step("suggest", suggest_generate),
    Step("save", suggest_save),
)
