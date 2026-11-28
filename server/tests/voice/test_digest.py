"""``voice.digest``: weekly then monthly, pause, periods, final texts as samples, the push, and
accepting suggestions into a new guide version."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from pydantic import JsonValue

from blogwriter.ai.types import Sensitivity, Task
from blogwriter.db import Filter
from blogwriter.domain.docstore import create_doc, find_docs, read_doc, read_singleton, upsert_doc
from blogwriter.domain.jobtypes import VOICE_DIGEST
from blogwriter.domain.posts import Post
from blogwriter.domain.settings import AutonomySettings, VoicePreferences
from blogwriter.domain.states import GuideStatus
from blogwriter.domain.testing import SAMPLE_NOW, FakeServices, ScriptedModelRunner, sample_document
from blogwriter.domain.voice import (
    GuideRules,
    GuideSuggestion,
    StyleGuide,
    VoiceDigest,
    VoiceSample,
)
from blogwriter.voice.digest import (
    DigestDraft,
    PostNotes,
    SuggestionDraft,
    diff_hunks,
    digest_period,
    edit_ratio,
    effective_cadence,
    is_due,
)
from blogwriter.voice.testing import run_job, seed_policy

CURLY = "\u2019"
ZONE = "America/New_York"
MONDAY = datetime(2026, 11, 30, 13, 30, tzinfo=UTC)  # 08:30 in New York
SLOT = datetime(2026, 11, 30, 13, 0, tzinfo=UTC)
BACKGROUND: dict[str, JsonValue] = {"origin": "background"}
OWNER: dict[str, JsonValue] = {"origin": "owner"}
DRAFT_1 = f"Let{CURLY}s dive in: we leveraged a robust plan to move 2,000 apps."
FINAL_1 = "We moved 2,000 apps in 14 months. The last 600 took most of the time."
DRAFT_2 = "Unpopular opinion: agents need their own identities."
FINAL_2 = "Agents need their own identities, not borrowed ones."
CURRENT_RULE = "Give the number with its unit, then what it changed."


def runner(services: FakeServices) -> ScriptedModelRunner:
    assert services.ai is not None
    return services.ai


async def add_post(
    services: FakeServices, posted_at: str, *, draft: str | None, final: str, pillar: str = "agents"
) -> str:
    post = sample_document(
        Post,
        status="posted",
        posted_at=posted_at,
        ai_draft_text=draft,
        final_text=final,
        display_text=final,
        pillar=pillar,
    )
    return (await create_doc(services.store, post)).doc.id


async def add_active_guide(services: FakeServices) -> StyleGuide:
    guide = sample_document(
        StyleGuide,
        id=StyleGuide.id_for(1),
        version=1,
        status="active",
        rules=GuideRules(prefer=[CURRENT_RULE], banned_phrases=["synergy"]),
    )
    return (await create_doc(services.store, guide)).doc


async def two_posts(services: FakeServices) -> tuple[str, str]:
    first = await add_post(
        services, "2026-11-24T15:00:00.000000Z", draft=DRAFT_1, final=FINAL_1, pillar="identity"
    )
    second = await add_post(services, "2026-11-26T15:00:00.000000Z", draft=DRAFT_2, final=FINAL_2)
    await add_post(services, "2026-11-30T10:00:00.000000Z", draft=DRAFT_2, final="Later.")
    await add_post(services, "2026-11-25T15:00:00.000000Z", draft=None, final="By hand.")
    return first, second


def digest_draft(first: str, second: str) -> DigestDraft:
    return DigestDraft(
        posts=[
            PostNotes(
                post_id=first,
                notable_edits=[
                    "Cut the opening 'Let's dive in'.",
                    "Replaced 'leveraged' with 'moved'.",
                ],
            ),
            PostNotes(post_id=second, notable_edits=["Dropped the formula opener."]),
        ],
        suggestions=[
            SuggestionDraft(
                kind="banned_phrase", text="unpopular opinion", rationale=f"{second} cut it."
            ),
            SuggestionDraft(
                kind="prefer", text="Start with the result and its number.", rationale="Both."
            ),
            SuggestionDraft(kind="prefer", text=CURRENT_RULE.upper(), rationale="Known rule."),
            SuggestionDraft(kind="vocabulary", text="use: never utilize", rationale="Both."),
            SuggestionDraft(kind="avoid", text="Northwind-style hype.", rationale="Blocklisted."),
        ],
    )


async def test_a_weekly_digest_end_to_end(services: FakeServices) -> None:
    services.clock.set(MONDAY)
    await seed_policy(services, terms=["Northwind"])
    await add_active_guide(services)
    first, second = await two_posts(services)
    runner(services).add(Task.SYNTHESIS, digest_draft(first, second))
    job = await run_job(services, VOICE_DIGEST, BACKGROUND, job_id="digest-1", scheduled_for=SLOT)
    assert job.status == "succeeded", job.error
    assert job.result == {
        "digestId": "voice-digest-2026-11-30",
        "comparisons": 2,
        "suggestions": 3,
        "pushed": True,
    }
    digest = (await read_doc(services.store, VoiceDigest, "voice-digest-2026-11-30")).doc
    assert digest.period_start == "2026-11-23T05:00:00.000000Z"
    assert digest.period_end == "2026-11-30T05:00:00.000000Z"
    assert digest.guide_version == 1
    assert [comparison.target_id for comparison in digest.comparisons] == [first, second]
    assert digest.comparisons[0].edit_ratio == edit_ratio(DRAFT_1, FINAL_1)
    assert digest.comparisons[0].notable_edits[1] == "Replaced 'leveraged' with 'moved'."
    assert [(s.id, s.kind, s.text) for s in digest.suggestions] == [
        ("s1", "banned_phrase", "unpopular opinion"),
        ("s2", "prefer", "Start with the result and its number."),
        ("s3", "vocabulary", "use: never utilize"),
    ]
    [record] = digest.served_by
    assert record.prompt_version == "voice.digest.v1"

    [call] = runner(services).calls_for(Task.SYNTHESIS)
    assert call.request.sensitivity is Sensitivity.ANONYMIZED
    assert call.request.tools == ()
    assert call.origin == "background"
    prompt = call.request.messages[0].text
    assert DRAFT_1 in prompt
    assert FINAL_2 in prompt
    assert "Later." not in prompt
    assert "By hand." not in prompt
    assert CURRENT_RULE in prompt

    [(payload, urgency)] = services.notifier.sent
    assert payload.title == "Voice digest ready"
    assert payload.url == "/library/voice?digest=voice-digest-2026-11-30"
    assert urgency == "low"

    samples = await find_docs(
        services.store, VoiceSample, [Filter("sourceType", "==", "final_post")]
    )
    assert sorted(stored.doc.source_ref or "" for stored in samples) == sorted([first, second])
    assert {stored.doc.text for stored in samples} == {FINAL_1, FINAL_2}


async def test_a_second_run_the_same_day_does_nothing(services: FakeServices) -> None:
    services.clock.set(MONDAY)
    await seed_policy(services)
    first, second = await two_posts(services)
    runner(services).add(Task.SYNTHESIS, digest_draft(first, second))
    await run_job(services, VOICE_DIGEST, BACKGROUND, job_id="digest-1", scheduled_for=SLOT)
    again = await run_job(services, VOICE_DIGEST, OWNER, job_id="digest-2")
    assert again.result == {"skipped": "exists", "digestId": "voice-digest-2026-11-30"}
    assert len(runner(services).calls_for(Task.SYNTHESIS)) == 1
    assert len(services.notifier.sent) == 1
    samples = await find_docs(services.store, VoiceSample)
    assert len(samples) == 2


async def test_the_period_starts_where_the_last_digest_ended(services: FakeServices) -> None:
    services.clock.set(MONDAY)
    await seed_policy(services)
    earlier = sample_document(
        VoiceDigest,
        id=VoiceDigest.id_for("2026-11-16"),
        period_start="2026-11-09T05:00:00.000000Z",
        period_end="2026-11-16T05:00:00.000000Z",
    )
    await create_doc(services.store, earlier)
    old = await add_post(services, "2026-11-18T15:00:00.000000Z", draft=DRAFT_2, final=FINAL_2)
    runner(services).add(Task.SYNTHESIS, DigestDraft(posts=[], suggestions=[]))
    job = await run_job(services, VOICE_DIGEST, BACKGROUND, job_id="digest-gap", scheduled_for=SLOT)
    assert job.status == "succeeded"
    digest = (await read_doc(services.store, VoiceDigest, "voice-digest-2026-11-30")).doc
    assert digest.period_start == "2026-11-16T05:00:00.000000Z"
    assert [comparison.target_id for comparison in digest.comparisons] == [old]
    assert digest.comparisons[0].notable_edits == []


async def test_no_posts_no_digest_no_push(services: FakeServices) -> None:
    services.clock.set(MONDAY)
    await seed_policy(services)
    job = await run_job(
        services, VOICE_DIGEST, BACKGROUND, job_id="digest-empty", scheduled_for=SLOT
    )
    assert job.status == "succeeded"
    assert isinstance(job.result, dict)
    assert job.result["skipped"] == "no_posts"
    assert runner(services).calls == []
    assert services.notifier.sent == []
    assert await find_docs(services.store, VoiceDigest) == []


async def test_pause_skips_scheduled_runs_only(services: FakeServices) -> None:
    services.clock.set(MONDAY)
    await seed_policy(services)
    await upsert_doc(services.store, sample_document(AutonomySettings, paused=True))
    first, second = await two_posts(services)
    paused = await run_job(
        services, VOICE_DIGEST, BACKGROUND, job_id="digest-p", scheduled_for=SLOT
    )
    assert paused.result == {"skipped": "paused"}
    assert runner(services).calls == []
    assert await find_docs(services.store, VoiceSample) == []
    runner(services).add(Task.SYNTHESIS, digest_draft(first, second))
    owner = await run_job(services, VOICE_DIGEST, OWNER, job_id="digest-o")
    assert owner.status == "succeeded"
    assert isinstance(owner.result, dict)
    assert owner.result["digestId"] == "voice-digest-2026-11-30"


async def set_weekly_until(services: FakeServices, stamp: str) -> None:
    # Singleton samples start from a camelCase dump: override them with camelCase names.
    await upsert_doc(services.store, sample_document(VoicePreferences, weeklyUntil=stamp))


async def test_weekly_turns_monthly_after_the_first_quarter(services: FakeServices) -> None:
    await seed_policy(services)
    await set_weekly_until(services, "2027-03-01T00:00:00.000000Z")
    mid_february = datetime(2027, 2, 15, 13, 0, tzinfo=UTC)
    services.clock.set(mid_february + timedelta(minutes=30))
    post = await add_post(services, "2027-02-10T15:00:00.000000Z", draft=DRAFT_1, final=FINAL_1)
    runner(services).add(Task.SYNTHESIS, DigestDraft(posts=[], suggestions=[]))
    weekly = await run_job(
        services, VOICE_DIGEST, BACKGROUND, job_id="digest-feb", scheduled_for=mid_february
    )
    assert isinstance(weekly.result, dict)
    assert weekly.result["digestId"] == "voice-digest-2027-02-15"
    assert post

    mid_march = datetime(2027, 3, 15, 12, 0, tzinfo=UTC)  # 08:00 EDT, day 15
    services.clock.set(mid_march + timedelta(minutes=30))
    await add_post(services, "2027-03-10T15:00:00.000000Z", draft=DRAFT_2, final=FINAL_2)
    skipped = await run_job(
        services, VOICE_DIGEST, BACKGROUND, job_id="digest-mar", scheduled_for=mid_march
    )
    assert skipped.result == {"skipped": "not_due", "cadence": "monthly"}
    preferences, _ = await read_singleton(services.store, VoicePreferences, now=SAMPLE_NOW)
    assert preferences.digest_cadence == "monthly"
    assert len(runner(services).calls) == 1

    first_week = datetime(2027, 4, 5, 12, 0, tzinfo=UTC)  # Monday, day 5
    services.clock.set(first_week + timedelta(minutes=30))
    runner(services).add(Task.SYNTHESIS, DigestDraft(posts=[], suggestions=[]))
    monthly = await run_job(
        services, VOICE_DIGEST, BACKGROUND, job_id="digest-apr", scheduled_for=first_week
    )
    assert isinstance(monthly.result, dict)
    assert monthly.result["digestId"] == "voice-digest-2027-04-05"
    digest = (await read_doc(services.store, VoiceDigest, "voice-digest-2027-04-05")).doc
    assert digest.period_start == "2027-02-15T05:00:00.000000Z"  # since the last digest
    assert len(digest.comparisons) == 1


async def test_the_owner_can_run_a_digest_mid_month(services: FakeServices) -> None:
    await seed_policy(services)
    await set_weekly_until(services, "2027-03-01T00:00:00.000000Z")
    services.clock.set(datetime(2027, 3, 17, 15, 0, tzinfo=UTC))
    first = await add_post(services, "2027-03-10T15:00:00.000000Z", draft=DRAFT_1, final=FINAL_1)
    runner(services).add(Task.SYNTHESIS, DigestDraft(posts=[], suggestions=[]))
    job = await run_job(services, VOICE_DIGEST, OWNER, job_id="digest-owner")
    assert isinstance(job.result, dict)
    assert job.result["digestId"] == "voice-digest-2027-03-17"
    assert first


async def test_texts_matching_the_blocklist_never_reach_the_model(
    services: FakeServices,
) -> None:
    services.clock.set(MONDAY)
    await seed_policy(services, terms=["Northwind"])
    hit = await add_post(
        services, "2026-11-24T15:00:00.000000Z", draft="Northwind asked for it.", final=FINAL_1
    )
    clean = await add_post(services, "2026-11-25T15:00:00.000000Z", draft=DRAFT_2, final=FINAL_2)
    runner(services).add(Task.SYNTHESIS, DigestDraft(posts=[], suggestions=[]))
    job = await run_job(services, VOICE_DIGEST, BACKGROUND, job_id="digest-bl", scheduled_for=SLOT)
    assert job.status == "succeeded"
    prompt = runner(services).calls_for(Task.SYNTHESIS)[0].request.messages[0].text
    assert "Northwind" not in prompt
    digest = (await read_doc(services.store, VoiceDigest, "voice-digest-2026-11-30")).doc
    assert [comparison.target_id for comparison in digest.comparisons] == [clean]
    assert hit != clean


@pytest.mark.parametrize(
    ("setup", "code"),
    [("blocklist", "blocklist_not_configured"), ("ai_use", "ai_use_not_confirmed")],
)
async def test_missing_owner_inputs_fail_without_a_model_call(
    services: FakeServices, setup: str, code: str
) -> None:
    services.clock.set(MONDAY)
    await seed_policy(
        services,
        blocklist_configured=setup != "blocklist",
        ai_use_confirmed=setup != "ai_use",
    )
    await two_posts(services)
    job = await run_job(services, VOICE_DIGEST, OWNER, job_id=f"digest-{setup}")
    assert job.status == "failed"
    assert job.error is not None
    assert job.error.code == code
    assert runner(services).calls == []


# --------------------------------------------------------------------------------------------
# Suggestions
# --------------------------------------------------------------------------------------------


async def stored_digest(services: FakeServices) -> VoiceDigest:
    digest = sample_document(
        VoiceDigest,
        id=VoiceDigest.id_for("2026-11-30"),
        guide_version=1,
        suggestions=[
            GuideSuggestion(id="s1", kind="prefer", text="Start with the result and its number."),
            GuideSuggestion(id="s2", kind="banned_phrase", text="unpopular opinion"),
            GuideSuggestion(id="s3", kind="prefer", text=CURRENT_RULE),
        ],
    )
    return (await create_doc(services.store, digest)).doc


async def decide(
    client: httpx.AsyncClient, headers: dict[str, str], sid: str, decision: str, etag: str | None
) -> httpx.Response:
    extra = {"If-Match": etag} if etag is not None else {}
    return await client.post(
        f"/api/voice/digests/voice-digest-2026-11-30/suggestions/{sid}",
        json={"decision": decision},
        headers={**headers, **extra},
    )


async def test_accepting_a_suggestion_proposes_a_new_version(
    services: FakeServices, client: httpx.AsyncClient, write_headers: dict[str, str]
) -> None:
    await seed_policy(services)
    base = await add_active_guide(services)
    await stored_digest(services)
    etag = (await read_doc(services.store, VoiceDigest, "voice-digest-2026-11-30")).etag
    assert (await decide(client, write_headers, "s1", "accept", None)).status_code == 428
    accepted = await decide(client, write_headers, "s1", "accept", etag)
    assert accepted.status_code == 200, accepted.text
    suggestion = accepted.json()["item"]["suggestions"][0]
    assert suggestion["status"] == "accepted"
    assert suggestion["decidedAt"] == SAMPLE_NOW
    proposed = (await read_doc(services.store, StyleGuide, StyleGuide.id_for(2))).doc
    assert proposed.status is GuideStatus.PROPOSED
    assert proposed.rules.prefer == [CURRENT_RULE, "Start with the result and its number."]
    assert proposed.rules.banned_phrases == base.rules.banned_phrases
    assert proposed.based_on_version == 1
    assert proposed.body == base.body
    assert proposed.edited_by_owner is False
    assert proposed.served_by == []

    etag = accepted.json()["etag"]
    twice = await decide(client, write_headers, "s1", "accept", etag)
    assert twice.status_code == 409
    assert twice.json()["code"] == "invalid_transition"
    dismissed = await decide(client, write_headers, "s2", "dismiss", etag)
    assert dismissed.json()["item"]["suggestions"][1]["status"] == "dismissed"
    known = await decide(client, write_headers, "s3", "accept", dismissed.json()["etag"])
    assert known.status_code == 200
    assert len(await find_docs(services.store, StyleGuide)) == 2  # the rule was already there
    missing = await decide(client, write_headers, "s9", "accept", known.json()["etag"])
    assert missing.status_code == 404
    stale = await decide(client, write_headers, "s2", "accept", '"stale"')
    assert stale.status_code == 412


async def test_accepting_needs_an_active_guide_and_a_clean_rule(
    services: FakeServices, client: httpx.AsyncClient, write_headers: dict[str, str]
) -> None:
    await seed_policy(services, terms=["unpopular"])
    await stored_digest(services)
    etag = (await read_doc(services.store, VoiceDigest, "voice-digest-2026-11-30")).etag
    missing = await decide(client, write_headers, "s1", "accept", etag)
    assert missing.status_code == 409
    assert missing.json()["code"] == "guide_missing"
    await add_active_guide(services)
    blocked = await decide(client, write_headers, "s2", "accept", etag)
    assert blocked.status_code == 422
    assert "unpopular" not in blocked.text
    unchanged = await read_doc(services.store, VoiceDigest, "voice-digest-2026-11-30")
    assert unchanged.etag == etag


async def test_digests_list_newest_first(
    services: FakeServices, client: httpx.AsyncClient, read_headers: dict[str, str]
) -> None:
    for end in ("2026-11-16", "2026-11-30", "2026-11-23"):
        stamp = f"{end}T05:00:00.000000Z"
        await create_doc(
            services.store,
            sample_document(VoiceDigest, id=VoiceDigest.id_for(end), period_end=stamp),
        )
    first = (
        await client.get("/api/voice/digests", params={"limit": 2}, headers=read_headers)
    ).json()
    assert [entry["item"]["id"] for entry in first["items"]] == [
        "voice-digest-2026-11-30",
        "voice-digest-2026-11-23",
    ]
    rest = await client.get(
        "/api/voice/digests", params={"cursor": first["nextCursor"]}, headers=read_headers
    )
    assert [entry["item"]["id"] for entry in rest.json()["items"]] == ["voice-digest-2026-11-16"]
    one = await client.get("/api/voice/digests/voice-digest-2026-11-23", headers=read_headers)
    assert one.json()["item"]["periodEnd"] == "2026-11-23T05:00:00.000000Z"
    assert one.headers["ETag"] == one.json()["etag"]


async def test_the_owner_can_start_a_digest(
    services: FakeServices, client: httpx.AsyncClient, write_headers: dict[str, str]
) -> None:
    await seed_policy(services)
    response = await client.post("/api/voice/digests/run", headers=write_headers)
    assert response.status_code == 202
    assert response.json()["jobId"] == "voice-digest-run-20261126"
    job = await services.jobs.get("voice-digest-run-20261126")
    assert (job.type, job.input) == (VOICE_DIGEST, OWNER)


# --------------------------------------------------------------------------------------------
# Pure rules
# --------------------------------------------------------------------------------------------


def test_cadence_and_due_days() -> None:
    weekly = sample_document(VoicePreferences)
    assert effective_cadence(weekly, MONDAY) == "weekly"
    until = sample_document(VoicePreferences, weeklyUntil="2027-03-01T00:00:00.000000Z")
    assert effective_cadence(until, datetime(2027, 2, 28, 23, tzinfo=UTC)) == "weekly"
    assert effective_cadence(until, datetime(2027, 3, 1, tzinfo=UTC)) == "monthly"
    switched = sample_document(VoicePreferences, digestCadence="monthly")
    assert effective_cadence(switched, MONDAY) == "monthly"
    assert is_due("weekly", datetime(2027, 3, 15).date())
    assert is_due("monthly", datetime(2027, 3, 7).date())
    assert not is_due("monthly", datetime(2027, 3, 8).date())


def test_periods_follow_the_local_calendar() -> None:
    # The week across the November DST change: local midnights, not 7 x 24 h.
    period = digest_period(
        run_at=datetime(2026, 11, 2, 13, 0, tzinfo=UTC),
        timezone=ZONE,
        cadence="weekly",
        last_end=None,
    )
    assert (period.start, period.end) == (
        "2026-10-26T04:00:00.000000Z",
        "2026-11-02T05:00:00.000000Z",
    )
    assert period.digest_id == "voice-digest-2026-11-02"
    monthly = digest_period(run_at=MONDAY, timezone=ZONE, cadence="monthly", last_end=None)
    assert monthly.start == "2026-10-26T04:00:00.000000Z"
    capped = digest_period(
        run_at=MONDAY, timezone=ZONE, cadence="weekly", last_end="2026-01-05T05:00:00.000000Z"
    )
    assert capped.start == "2026-09-29T04:00:00.000000Z"  # at most 62 days back
    later = digest_period(
        run_at=MONDAY, timezone=ZONE, cadence="weekly", last_end="2026-12-07T05:00:00.000000Z"
    )
    assert later.start == "2026-11-23T05:00:00.000000Z"


def test_edit_ratio_and_hunks() -> None:
    assert edit_ratio("Same words here.", "same words HERE") == 0.0
    assert edit_ratio("alpha beta", "gamma delta") == 1.0
    assert edit_ratio("", "") == 0.0
    ratio = edit_ratio(DRAFT_1, FINAL_1)
    assert 0 < ratio < 1
    hunks = diff_hunks("We leveraged a robust plan today.", "We used a plan today.")
    assert hunks == [("leveraged", "used"), ("robust", "")]
