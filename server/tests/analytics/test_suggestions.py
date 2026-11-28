"""``analytics.suggest``: public aggregates only, suggestions never change plans or settings,
ids kept on resume, pause, the blocklist on the values sent, and the owner's decision."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
from pydantic import JsonValue

from blogwriter.ai.types import Sensitivity, Task
from blogwriter.analytics.rates import (
    InsightDimension,
    InsightsOverview,
    InsightsRow,
    MetricRates,
)
from blogwriter.analytics.suggestions import (
    MAX_PER_DIMENSION,
    InsightDraft,
    InsightsDraft,
    chosen_suggestions,
    has_tell,
)
from blogwriter.analytics.testing import (
    analytics_registry,
    posted_post,
    run_job,
    run_until_settled,
    seed_policy,
    snapshot,
)
from blogwriter.db import CONTAINERS, Store
from blogwriter.domain.analytics import InsightSuggestion
from blogwriter.domain.docstore import create_doc, find_docs, read_doc, upsert_doc
from blogwriter.domain.jobtypes import ANALYTICS_SUGGEST
from blogwriter.domain.settings import AutonomySettings
from blogwriter.domain.signals import Idea
from blogwriter.domain.testing import SAMPLE_NOW, FakeServices, ScriptedModelRunner, sample_document
from blogwriter.jobs import Job
from blogwriter.jobs.queue import enqueue
from blogwriter.signals import repurpose_idea_id

MONDAY = datetime(2026, 11, 30, 12, 30, tzinfo=UTC)  # 07:30 in New York
WEEK = "2026-11-30"
BACKGROUND: dict[str, JsonValue] = {"origin": "background"}
OWNER: dict[str, JsonValue] = {"origin": "owner"}
POST_TEXT = "We moved 2,000 apps in 14 months; the last 600 took most of the time."


def runner(services: FakeServices) -> ScriptedModelRunner:
    assert services.ai is not None
    return services.ai


async def seed_measured(services: FakeServices, *, pillar: str = "agents") -> None:
    """Eight live posts with +7 d numbers (carousels beat text posts on saves)."""
    for index in range(8):
        carousel = index % 2 == 0
        await posted_post(
            services,
            posted_at=f"2026-11-{10 + index:02d}T15:00:00.000000Z",
            pillar=pillar if index < 6 else "identity",
            format="carousel" if carousel else "text",
            display_text=POST_TEXT,
            final_text=POST_TEXT,
            snapshots=[
                snapshot(
                    taken_at=f"2026-11-{17 + index:02d}T15:00:00.000000Z",
                    impressions=1000 + 100 * index,
                    reactions=20 + index,
                    comments=3,
                    saves=12 if carousel else 4,
                )
            ],
        )


def draft() -> InsightsDraft:
    return InsightsDraft(
        suggestions=[
            InsightDraft(
                dimension="format",
                value="carousel",
                text="Carousels drew 12 saves per 1K against your 8 median over 4 posts. "
                "Try one more carousel a week for a month.",
            ),
            InsightDraft(dimension="format", value="video", text="Post a video."),  # no line
            InsightDraft(
                dimension="pillar",
                value="agents",
                text="Leverage your agents pillar for crucial reach.",  # AI tells
            ),
            InsightDraft(
                dimension="format",
                value="text",
                text="Text posts got 4 saves per 1K. Add one concrete number to each.",
            ),
            InsightDraft(dimension="format", value="text", text="A third format note."),  # >2
            InsightDraft(
                dimension="pillar",
                value="identity",
                text="Identity posts ran 10% under your median on saves over 2 posts. Test it.",
            ),
        ]
    )


async def store_state(store: Store) -> dict[str, dict[str, Any]]:
    """Every stored document outside ``jobs`` (bodies by container and id)."""
    state: dict[str, dict[str, Any]] = {}
    for container in CONTAINERS:
        if container == "jobs":
            continue
        for doc in await store.find(container, []):
            state[f"{container}/{doc.id}"] = doc.body
    return state


async def test_a_weekly_run_saves_suggestions_and_changes_nothing_else(
    services: FakeServices,
) -> None:
    services.clock.set(MONDAY)
    await seed_policy(services)
    await seed_measured(services)
    await upsert_doc(services.store, AutonomySettings.default(SAMPLE_NOW))
    runner(services).add(Task.SYNTHESIS, draft())
    before = await store_state(services.store)
    job = await run_job(
        services,
        ANALYTICS_SUGGEST,
        BACKGROUND,
        job_id="sched-analytics-suggest-20261130T123000Z",
        scheduled_for=MONDAY,
    )
    assert job.status == "succeeded", job.error
    expected = [
        InsightSuggestion.id_for(WEEK, "format", 0),
        InsightSuggestion.id_for(WEEK, "format", 1),
        InsightSuggestion.id_for(WEEK, "pillar", 0),
    ]
    assert job.result == {"weekStart": WEEK, "suggestionIds": expected, "count": 3}
    after = await store_state(services.store)
    added = {key for key in after if key not in before}
    ideas = {key for key in added if key.startswith("ideas/")}
    assert added - ideas == {f"settings/{suggestion_id}" for suggestion_id in expected}
    # Besides the suggestions, only repurpose ideas for the owner's inbox (posts that beat the
    # median become flagship candidates); nothing is generated from them without the owner.
    assert ideas
    for key in ideas:
        assert after[key]["origin"] == "repurpose"
        assert after[key]["suggestedLane"] == "flagship"
        assert f"posts/{after[key]['repurposeOf']}" in before
    assert {key: after[key] for key in before} == before  # no plan, setting or post changed
    first = (await read_doc(services.store, InsightSuggestion, expected[0])).doc
    assert first.status.value == "proposed"
    assert first.value == "carousel"
    assert "value" not in (first.model_extra or {})
    assert first.week_start == WEEK
    assert first.evidence is not None
    assert first.evidence.startswith("format=carousel, 4 posts; reactions ")
    assert first.served_by is not None
    assert first.served_by.prompt_version == "analytics.suggest.v1"
    [call] = runner(services).calls_for(Task.SYNTHESIS)
    assert call.origin == "background"
    assert call.request.sensitivity is Sensitivity.PUBLIC
    assert call.request.tools == ()
    sent = "\n".join(message.text for message in call.request.messages)
    assert "dimension=format value=carousel posts=4" in sent
    assert "2,000 apps" not in sent  # aggregates only, never post text
    assert all(event.type != "input.requested" for event in job.events)
    assert "Try one more carousel" not in job.model_dump_json()  # text lives on documents only


async def test_suggestions_keep_their_ids_on_resume_and_on_a_second_run(
    services: FakeServices,
) -> None:
    services.clock.set(MONDAY)
    await seed_policy(services)
    await seed_measured(services)
    runner(services).add(Task.SYNTHESIS, draft())
    first = await run_job(services, ANALYTICS_SUGGEST, OWNER, job_id="suggest-1")
    again = await run_job(services, ANALYTICS_SUGGEST, OWNER, job_id="suggest-2")
    assert again.status == "succeeded"
    assert isinstance(first.result, dict)
    assert isinstance(again.result, dict)
    assert again.result["suggestionIds"] == first.result["suggestionIds"]
    assert len(runner(services).calls_for(Task.SYNTHESIS)) == 1  # never asked twice a week
    stored = await find_docs(services.store, InsightSuggestion)
    assert len(stored) == 3


async def test_a_resumed_suggest_step_reuses_what_it_saved(services: FakeServices) -> None:
    """The process died after ``suggest`` saved the week's suggestions but before the step was
    committed: the resumed step finds them and asks no model again."""
    services.clock.set(MONDAY)
    await seed_policy(services)
    await seed_measured(services)
    runner(services).add(Task.SYNTHESIS, draft())
    first = await run_job(services, ANALYTICS_SUGGEST, OWNER, job_id="suggest-first")
    assert isinstance(first.result, dict)
    compute_output = next(step.output for step in first.steps if step.name == "compute")
    registry = analytics_registry()
    definition = registry.get(ANALYTICS_SUGGEST)
    assert definition is not None
    await enqueue(
        services.store,
        ANALYTICS_SUGGEST,
        OWNER,
        job_id="suggest-resumed",
        steps=definition.step_names,
        clock=services.clock,
    )
    raw = await services.store.read("jobs", "suggest-resumed")
    crashed = Job.from_body(raw.body)
    for step in crashed.steps:
        if step.name == "compute":
            step.status = "succeeded"
            step.output = compute_output
        elif step.name == "suggest":
            step.runs = 1  # it ran before the crash
    await services.store.replace("jobs", crashed.to_body(), if_match=raw.etag)
    resumed = await run_until_settled(services, "suggest-resumed", registry=registry)
    assert resumed.status == "succeeded"
    assert isinstance(resumed.result, dict)
    assert resumed.result["suggestionIds"] == first.result["suggestionIds"]
    assert len(runner(services).calls_for(Task.SYNTHESIS)) == 1


async def test_paused_background_runs_skip_and_owner_runs_go(services: FakeServices) -> None:
    services.clock.set(MONDAY)
    await seed_policy(services)
    await seed_measured(services)
    paused = AutonomySettings.default(SAMPLE_NOW).model_copy(
        update={"paused": True, "paused_at": SAMPLE_NOW}
    )
    await upsert_doc(services.store, paused)
    skipped = await run_job(services, ANALYTICS_SUGGEST, BACKGROUND, job_id="suggest-bg")
    assert skipped.result == {"skipped": "paused"}
    assert runner(services).calls_for(Task.SYNTHESIS) == []
    runner(services).add(Task.SYNTHESIS, draft())
    owner = await run_job(services, ANALYTICS_SUGGEST, OWNER, job_id="suggest-owner")
    assert owner.status == "succeeded"
    assert runner(services).calls_for(Task.SYNTHESIS)[0].origin == "owner"


async def test_too_little_data_suggests_nothing(services: FakeServices) -> None:
    await seed_policy(services)
    await posted_post(
        services,
        posted_at="2026-11-20T15:00:00.000000Z",
        snapshots=[snapshot(taken_at="2026-11-22T15:00:00.000000Z", impressions=100)],
    )
    job = await run_job(services, ANALYTICS_SUGGEST, OWNER, job_id="suggest-thin")
    assert job.result == {"skipped": "not_enough_data", "weekStart": "2026-11-23", "posts": 1}
    assert runner(services).calls_for(Task.SYNTHESIS) == []


async def test_the_blocklist_guards_the_values_sent(services: FakeServices) -> None:
    await seed_policy(services, terms=["Northwind"])
    await seed_measured(services, pillar="northwind")
    hit = await run_job(services, ANALYTICS_SUGGEST, OWNER, job_id="suggest-hit")
    assert hit.status == "failed"
    assert hit.error is not None
    assert hit.error.code == "blocklist_hit"
    await seed_policy(services, blocklist_configured=False)
    unset = await run_job(services, ANALYTICS_SUGGEST, OWNER, job_id="suggest-unset")
    assert unset.error is not None
    assert unset.error.code == "blocklist_not_configured"
    assert runner(services).calls_for(Task.SYNTHESIS) == []


async def test_no_model_fails_ai_unavailable(services: FakeServices) -> None:
    services.ai = None
    await seed_policy(services)
    await seed_measured(services)
    job = await run_job(services, ANALYTICS_SUGGEST, OWNER, job_id="suggest-no-ai")
    assert job.error is not None
    assert job.error.code == "ai_unavailable"


def overview(*rows: tuple[InsightDimension, str, int]) -> InsightsOverview:
    rates = MetricRates(reactions=10.0, saves=2.0)
    return InsightsOverview(
        weeks=12,
        since="2026-09-03T17:00:00.000000Z",
        posts=10,
        owner_median=rates,
        rows=[
            InsightsRow(
                dimension=dimension,
                value=value,
                posts=posts,
                rates_per_1k=rates,
                vs_median=MetricRates(reactions=0.0),
            )
            for dimension, value, posts in rows
        ],
    )


def test_the_rules_for_keeping_suggestions() -> None:
    lines = overview(("format", "carousel", 4), ("format", "text", 3), ("lane", "native", 1))
    kept = chosen_suggestions(
        InsightsDraft(
            suggestions=[
                InsightDraft(dimension="format", value="carousel", text="  Try  carousels. "),
                InsightDraft(dimension="lane", value="native", text="One post is not a line."),
                InsightDraft(dimension="format", value="text", text="x" * 501),
                InsightDraft(dimension="format", value="text", text="Here's the thing: no."),
                InsightDraft(dimension="format", value="text", text="Add a number."),
                InsightDraft(dimension="format", value="carousel", text="Over the cap."),
            ]
        ),
        lines,
    )
    assert [(item.dimension, item.ordinal, item.text) for item in kept] == [
        ("format", 0, "Try carousels."),
        ("format", 1, "Add a number."),
    ]
    assert MAX_PER_DIMENSION == 2
    assert has_tell("It\u2019s worth noting that carousels win.")  # a curly apostrophe
    assert has_tell("Unpopular opinion: carousels win.")
    assert not has_tell("Carousels drew 12 saves per 1K over 4 posts.")


async def test_deciding_records_the_decision_only(
    services: FakeServices, client: httpx.AsyncClient, write_headers: dict[str, str]
) -> None:
    suggestion = sample_document(InsightSuggestion, id=InsightSuggestion.id_for(WEEK, "format", 0))
    stored = await create_doc(services.store, suggestion)
    before = await store_state(services.store)
    url = f"/api/analytics/suggestions/{suggestion.id}/decide"
    accepted = await client.post(
        url, json={"decision": "accept"}, headers={**write_headers, "If-Match": stored.etag}
    )
    assert accepted.status_code == 200, accepted.text
    item = accepted.json()["item"]
    assert (item["status"], item["decidedAt"]) == ("accepted", "2026-11-26T17:00:00.000000Z")
    after = await store_state(services.store)
    assert set(after) == set(before)
    changed = {key for key in after if after[key] != before[key]}
    assert changed == {f"settings/{suggestion.id}"}
    twice = await client.post(
        url,
        json={"decision": "dismiss"},
        headers={**write_headers, "If-Match": accepted.json()["etag"]},
    )
    assert twice.status_code == 409
    assert twice.json()["code"] == "invalid_transition"
    missing = await client.post(url, json={"decision": "accept"}, headers=write_headers)
    assert missing.status_code == 428


async def test_listing_and_running(
    services: FakeServices,
    client: httpx.AsyncClient,
    write_headers: dict[str, str],
    read_headers: dict[str, str],
) -> None:
    for index, dimension in enumerate(("format", "pillar")):
        await create_doc(
            services.store,
            sample_document(
                InsightSuggestion,
                id=InsightSuggestion.id_for(WEEK, dimension, 0),
                dimension=dimension,
                created_at=f"2026-11-26T1{index}:00:00.000000Z",
            ),
        )
    listed = await client.get(
        "/api/analytics/suggestions",
        params={"status": "proposed", "limit": 1},
        headers=read_headers,
    )
    page = listed.json()
    assert [entry["item"]["dimension"] for entry in page["items"]] == ["pillar"]
    rest = await client.get(
        "/api/analytics/suggestions",
        params={"limit": 1, "cursor": page["nextCursor"]},
        headers=read_headers,
    )
    assert [entry["item"]["dimension"] for entry in rest.json()["items"]] == ["format"]
    assert rest.json()["nextCursor"] is None
    started = await client.post("/api/analytics/suggestions/run", headers=write_headers)
    assert started.status_code == 202
    assert started.json() == {"jobId": "analytics-suggest-run-20261126", "status": "queued"}
    again = await client.post("/api/analytics/suggestions/run", headers=write_headers)
    assert again.json()["jobId"] == "analytics-suggest-run-20261126"
    job = await services.jobs.get("analytics-suggest-run-20261126")
    assert job.input == {"origin": "owner"}


async def test_evergreen_posts_eight_to_twelve_weeks_old_get_a_re_cut_idea(
    services: FakeServices,
) -> None:
    """The repurpose lane needs a repurpose idea: the weekly run proposes re-cuts of evergreen
    posts (not quick takes, not time-sensitive) from 8-12 weeks ago, once each."""
    services.clock.set(MONDAY)
    await seed_policy(services)
    evergreen = await posted_post(
        services, posted_at="2026-09-21T15:00:00.000000Z", lane="native", format="carousel"
    )
    quick = await posted_post(services, posted_at="2026-09-22T15:00:00.000000Z", lane="quick_take")
    recent = await posted_post(services, posted_at="2026-11-02T15:00:00.000000Z", lane="native")
    job = await run_job(services, ANALYTICS_SUGGEST, OWNER, job_id="suggest-recut")
    assert job.status == "succeeded", job.error
    stored = await find_docs(services.store, Idea, [])
    ideas = {stored_idea.doc.repurpose_of: stored_idea.doc for stored_idea in stored}
    assert set(ideas) == {evergreen.id}
    idea = ideas[evergreen.id]
    assert idea.id == repurpose_idea_id(evergreen.id, "recut")
    assert (idea.origin, idea.suggested_lane, idea.suggested_format) == (
        "repurpose",
        "repurpose",
        "carousel",
    )
    assert quick.id not in ideas
    assert recent.id not in ideas
    again = await run_job(services, ANALYTICS_SUGGEST, OWNER, job_id="suggest-recut-2")
    assert again.status == "succeeded"
    assert len(await find_docs(services.store, Idea, [])) == 1  # proposed once
