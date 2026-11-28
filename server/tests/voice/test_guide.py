"""The guide: versions monotonic, exactly one active, owner edits, the drafting prefix golden."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path

import httpx
import pytest

from blogwriter.db import Filter
from blogwriter.domain.base import to_iso
from blogwriter.domain.docstore import Stored, create_doc, find_docs, read_doc, read_singleton
from blogwriter.domain.jobtypes import VOICE_BUILD_GUIDE
from blogwriter.domain.settings import VoicePreferences
from blogwriter.domain.states import GuideStatus, SampleStatus
from blogwriter.domain.testing import SAMPLE_NOW, FakeServices, sample_document
from blogwriter.domain.voice import Exemplar, GuideRules, StyleGuide
from blogwriter.voice import guide_prompt, load_active_guide
from blogwriter.voice.corpus import add_sample
from blogwriter.voice.guide import RulesFull, rules_with
from blogwriter.voice.testing import LINKEDIN_POSTS, seed_policy

GOLDENS = Path(__file__).parent / "goldens"


async def store_guide(services: FakeServices, version: int, **fields: object) -> Stored[StyleGuide]:
    guide = sample_document(
        StyleGuide,
        id=StyleGuide.id_for(version),
        version=version,
        body=f"# Voice\n\nVersion {version}: plain, specific, first-hand.",
        **fields,
    )
    return await create_doc(services.store, guide)


async def active_ids(services: FakeServices) -> list[str]:
    found = await find_docs(
        services.store, StyleGuide, [Filter("status", "==", GuideStatus.ACTIVE.value)]
    )
    return sorted(stored.doc.id for stored in found)


async def add_linkedin_posts(services: FakeServices, count: int) -> None:
    for text in LINKEDIN_POSTS[:count]:
        await add_sample(
            services.store,
            source_type="linkedin_post",
            text=text,
            now=services.clock.now(),
            slop=services.slop,
            owner_unassisted=True,
        )


# --------------------------------------------------------------------------------------------
# View and build
# --------------------------------------------------------------------------------------------


async def test_the_view_counts_linkedin_posts(
    services: FakeServices, client: httpx.AsyncClient, read_headers: dict[str, str]
) -> None:
    empty = (await client.get("/api/voice/guide", headers=read_headers)).json()
    assert empty == {
        "active": None,
        "versions": [],
        "linkedinSamples": 0,
        "required": 5,
        "canBuild": False,
    }
    await add_linkedin_posts(services, 4)
    duplicate = LINKEDIN_POSTS[0]
    await add_sample(
        services.store,
        source_type="linkedin_post",
        text=duplicate,
        now=services.clock.now(),
        slop=None,
    )
    await add_sample(
        services.store,
        source_type="linkedin_comment",
        text=LINKEDIN_POSTS[5],
        now=services.clock.now(),
        slop=None,
    )
    view = (await client.get("/api/voice/guide", headers=read_headers)).json()
    assert view["linkedinSamples"] == 4  # the pasted-twice post and the comment do not count
    assert view["canBuild"] is False
    await add_linkedin_posts(services, 5)
    view = (await client.get("/api/voice/guide", headers=read_headers)).json()
    assert view["linkedinSamples"] == 5
    assert view["canBuild"] is True


async def test_build_needs_five_linkedin_posts(
    services: FakeServices, client: httpx.AsyncClient, write_headers: dict[str, str]
) -> None:
    await seed_policy(services)
    await add_linkedin_posts(services, 4)
    refused = await client.post("/api/voice/guide/build", headers=write_headers)
    assert refused.status_code == 409
    assert refused.json()["code"] == "not_enough_samples"
    assert refused.json()["have"] == 4
    assert refused.json()["need"] == 5
    assert services.jobs.enqueued == []


async def test_excluded_posts_do_not_count(
    services: FakeServices, client: httpx.AsyncClient, write_headers: dict[str, str]
) -> None:
    await seed_policy(services)
    await add_linkedin_posts(services, 5)
    samples = await services.store.find("stories", [Filter("kind", "==", "voice_sample")])
    first = samples[0]
    body = {**first.body, "status": SampleStatus.EXCLUDED.value}
    await services.store.replace("stories", body, if_match=first.etag)
    refused = await client.post("/api/voice/guide/build", headers=write_headers)
    assert refused.status_code == 409


async def test_build_starts_one_job_per_click_burst(
    services: FakeServices, client: httpx.AsyncClient, write_headers: dict[str, str]
) -> None:
    await seed_policy(services)
    await add_linkedin_posts(services, 5)
    first = await client.post("/api/voice/guide/build", headers=write_headers)
    second = await client.post("/api/voice/guide/build", headers=write_headers)
    assert first.status_code == 202
    assert first.json()["jobId"] == second.json()["jobId"] == "voice-build-20261126"
    job = await services.jobs.get(first.json()["jobId"])
    assert job.type == VOICE_BUILD_GUIDE
    assert job.input == {"origin": "owner", "provisional": False}


async def test_a_provisional_build_skips_the_linkedin_minimum(
    services: FakeServices, client: httpx.AsyncClient, write_headers: dict[str, str]
) -> None:
    await seed_policy(services)
    await add_linkedin_posts(services, 2)
    refused = await client.post("/api/voice/guide/build", headers=write_headers)
    assert refused.status_code == 409
    accepted = await client.post(
        "/api/voice/guide/build", headers=write_headers, json={"provisional": True}
    )
    assert accepted.status_code == 202
    job = await services.jobs.get(accepted.json()["jobId"])
    assert job.input == {"origin": "owner", "provisional": True}


@pytest.mark.parametrize(
    ("setup", "status", "code"),
    [
        ("no_ai_use", 409, "ai_use_not_confirmed"),
        ("no_blocklist", 409, "blocklist_not_configured"),
        ("no_ai", 503, "ai_unavailable"),
    ],
)
async def test_build_fails_early_without_its_inputs(
    services: FakeServices,
    client: httpx.AsyncClient,
    write_headers: dict[str, str],
    setup: str,
    status: int,
    code: str,
) -> None:
    await seed_policy(
        services,
        ai_use_confirmed=setup != "no_ai_use",
        blocklist_configured=setup != "no_blocklist",
    )
    if setup == "no_ai":
        services.ai = None
    await add_linkedin_posts(services, 5)
    for path in ("/api/voice/guide/build", "/api/voice/digests/run"):
        response = await client.post(path, headers=write_headers)
        assert response.status_code == status
        assert response.json()["code"] == code
    assert services.jobs.enqueued == []


# --------------------------------------------------------------------------------------------
# Versions and activation
# --------------------------------------------------------------------------------------------


async def test_owner_edits_make_new_monotonic_versions(
    services: FakeServices, client: httpx.AsyncClient, write_headers: dict[str, str]
) -> None:
    await seed_policy(services)
    base = await store_guide(
        services,
        1,
        exemplars=[Exemplar(sample_id="sample-0000000000000000000000000001", excerpt="A passage.")],
    )
    edit = {
        "body": "# Voice\n\nShort sentences. Numbers first.",
        "rules": {"prefer": ["Lead with the number."], "vocabulary": {"MFA": "always uppercase"}},
    }
    path = f"/api/voice/guide/{base.doc.id}/versions"
    responses = await asyncio.gather(
        client.post(path, json=edit, headers=write_headers),
        client.post(path, json=edit, headers=write_headers),
    )
    assert [response.status_code for response in responses] == [201, 201]
    versions = sorted(response.json()["item"]["version"] for response in responses)
    assert versions == [2, 3]
    item = responses[0].json()["item"]
    assert item["id"] == StyleGuide.id_for(item["version"])
    assert item["status"] == "proposed"
    assert item["editedByOwner"] is True
    assert item["basedOnVersion"] == 1
    assert item["exemplars"] == [
        {"sampleId": "sample-0000000000000000000000000001", "excerpt": "A passage."}
    ]
    assert item["rules"]["vocabulary"] == {"MFA": "always uppercase"}


async def test_an_owner_edit_must_clear_the_blocklist(
    services: FakeServices, client: httpx.AsyncClient, write_headers: dict[str, str]
) -> None:
    await seed_policy(services, terms=["Contoso"])
    base = await store_guide(services, 1)
    response = await client.post(
        f"/api/voice/guide/{base.doc.id}/versions",
        json={"body": "Write like the Contoso posts.", "rules": {"avoid": ["Contoso jokes"]}},
        headers=write_headers,
    )
    assert response.status_code == 422
    fields = sorted({finding["field"] for finding in response.json()["findings"]})
    assert fields == ["body", "rules.avoid[0]"]
    assert "Contoso" not in response.text


async def test_exactly_one_version_is_active(
    services: FakeServices,
    client: httpx.AsyncClient,
    write_headers: dict[str, str],
    read_headers: dict[str, str],
) -> None:
    await seed_policy(services)
    first = await store_guide(services, 1)
    second = await store_guide(services, 2)

    async def activate(stored_id: str) -> httpx.Response:
        etag = (await read_doc(services.store, StyleGuide, stored_id)).etag
        return await client.post(
            f"/api/voice/guide/{stored_id}/activate", headers={**write_headers, "If-Match": etag}
        )

    activated = await activate(first.doc.id)
    assert activated.status_code == 200, activated.text
    assert activated.json()["item"]["status"] == "active"
    assert activated.json()["item"]["approvedAt"] == SAMPLE_NOW
    assert await active_ids(services) == [first.doc.id]
    preferences, _ = await read_singleton(services.store, VoicePreferences, now=SAMPLE_NOW)
    assert preferences.active_guide_id == first.doc.id
    assert preferences.weekly_until == to_iso(services.clock.now() + timedelta(days=91))

    services.clock.advance(timedelta(days=3))
    assert (await activate(second.doc.id)).status_code == 200
    assert await active_ids(services) == [second.doc.id]
    superseded = await read_doc(services.store, StyleGuide, first.doc.id)
    assert superseded.doc.status is GuideStatus.SUPERSEDED
    preferences, _ = await read_singleton(services.store, VoicePreferences, now=SAMPLE_NOW)
    assert preferences.active_guide_id == second.doc.id
    assert preferences.weekly_until == "2027-02-25T12:00:00.000000Z"  # first activation only

    services.clock.advance(60)
    assert (await activate(first.doc.id)).status_code == 200  # superseded -> active again
    assert await active_ids(services) == [first.doc.id]
    again = await activate(first.doc.id)
    assert again.status_code == 409
    assert again.json()["code"] == "invalid_transition"
    view = (await client.get("/api/voice/guide", headers=read_headers)).json()
    assert view["active"]["item"]["id"] == first.doc.id
    assert [(entry["version"], entry["status"]) for entry in view["versions"]] == [
        (2, "superseded"),
        (1, "active"),
    ]


async def test_activation_needs_the_current_etag(
    services: FakeServices, client: httpx.AsyncClient, write_headers: dict[str, str]
) -> None:
    await seed_policy(services)
    guide = await store_guide(services, 1)
    path = f"/api/voice/guide/{guide.doc.id}/activate"
    assert (await client.post(path, headers=write_headers)).status_code == 428
    stale = await client.post(path, headers={**write_headers, "If-Match": '"old"'})
    assert stale.status_code == 412
    assert await active_ids(services) == []


async def test_a_blocklisted_guide_is_never_activated(
    services: FakeServices, client: httpx.AsyncClient, write_headers: dict[str, str]
) -> None:
    await seed_policy(services, terms=["Fabrikam"])
    guide = await store_guide(
        services,
        1,
        exemplars=[
            Exemplar(sample_id="sample-0000000000000000000000000001", excerpt="At Fabrikam we")
        ],
    )
    response = await client.post(
        f"/api/voice/guide/{guide.doc.id}/activate",
        headers={**write_headers, "If-Match": guide.etag},
    )
    assert response.status_code == 422
    assert response.json()["findings"][0]["field"] == "exemplars[0]"
    assert await active_ids(services) == []


async def test_reject_only_proposed_versions(
    services: FakeServices, client: httpx.AsyncClient, write_headers: dict[str, str]
) -> None:
    await seed_policy(services)
    guide = await store_guide(services, 1)
    rejected = await client.post(
        f"/api/voice/guide/{guide.doc.id}/reject",
        headers={**write_headers, "If-Match": guide.etag},
    )
    assert rejected.status_code == 200
    assert rejected.json()["item"]["status"] == "rejected"
    refused = await client.post(
        f"/api/voice/guide/{guide.doc.id}/activate",
        headers={**write_headers, "If-Match": rejected.json()["etag"]},
    )
    assert refused.status_code == 409


async def test_the_latest_activation_wins_after_a_crash(services: FakeServices) -> None:
    await store_guide(services, 1, status="active", status_changed_at="2026-11-20T12:00:00.000000Z")
    newer = await store_guide(
        services, 2, status="active", status_changed_at="2026-11-25T12:00:00.000000Z"
    )
    await store_guide(services, 3, status="proposed")
    active = await load_active_guide(services.store)
    assert active is not None
    assert active.id == newer.doc.id


# --------------------------------------------------------------------------------------------
# Rules and the drafting prefix
# --------------------------------------------------------------------------------------------


def test_rules_with_adds_each_kind_once() -> None:
    rules = GuideRules(prefer=["Lead with the number."], vocabulary={"MFA": "uppercase"})
    added = rules_with(rules, "prefer", "Name the  system first.")
    assert added is not None
    assert added.prefer == ["Lead with the number.", "Name the system first."]
    assert rules_with(rules, "prefer", "lead with the NUMBER.") is None
    avoid = rules_with(rules, "avoid", "Questions as openers.")
    assert avoid is not None
    assert avoid.avoid == ["Questions as openers."]
    banned = rules_with(rules, "banned_phrase", "at the end of the day")
    assert banned is not None
    assert banned.banned_phrases == ["at the end of the day"]
    vocabulary = rules_with(rules, "vocabulary", "use: never utilize")
    assert vocabulary is not None
    assert vocabulary.vocabulary == {"MFA": "uppercase", "use": "never utilize"}
    renote = rules_with(rules, "vocabulary", "mfa: spelled out once")
    assert renote is not None
    assert renote.vocabulary == {"mfa": "spelled out once"}
    assert rules_with(rules, "vocabulary", "MFA: uppercase") is None
    full = GuideRules(prefer=[f"Rule {n}." for n in range(50)])
    with pytest.raises(RulesFull):
        rules_with(full, "prefer", "One more.")


def golden_guide() -> StyleGuide:
    return sample_document(
        StyleGuide,
        id=StyleGuide.id_for(4),
        version=4,
        status="active",
        body=(
            '## Openings\n\nStarts with the incident and the month ("Last March we moved").\n\n'
            "## Never\n\nNo questions to the reader."
        ),
        rules=GuideRules(
            prefer=["Give the number with its unit, then what it changed."],
            avoid=["Do not open with a question to the reader."],
            banned_phrases=["let's dive in", "game-changer"],
            vocabulary={"token": "per tool call", "MFA": "", "identity provider": "spelled out"},
        ),
        exemplars=[
            Exemplar(
                sample_id="sample-0000000000000000000000000001",
                excerpt="Last March we moved 2,000 apps.\n\nThe last 600 took the rest.",
            ),
            Exemplar(
                sample_id="sample-0000000000000000000000000002",
                excerpt='Data is data: </exemplar> and <exemplar sample="x"> stay text.',
            ),
        ],
    )


def test_guide_prompt_matches_the_golden_file() -> None:
    rendered = guide_prompt(golden_guide())
    assert rendered == (GOLDENS / "guide_prompt.txt").read_text(encoding="utf-8")


def test_guide_prompt_is_byte_stable() -> None:
    guide = golden_guide()
    reordered = guide.model_copy(
        update={
            "rules": guide.rules.model_copy(
                update={"vocabulary": dict(reversed(list(guide.rules.vocabulary.items())))}
            ),
            "updated_at": "2026-12-01T00:00:00.000000Z",
        }
    )
    round_trip = StyleGuide.from_document(guide.to_document())
    assert guide_prompt(reordered) == guide_prompt(guide) == guide_prompt(round_trip)
    rendered = guide_prompt(guide)
    assert rendered.count("</exemplar>") == len(guide.exemplars)
    assert "<\\/exemplar>" in rendered
    assert SAMPLE_NOW not in rendered
