"""The illustration routes: starting a run (every gate, the run named on the article before it is
enqueued) and the owner's actions on candidates (hero, back to the card, insert after a heading,
alt text, discard) with strict If-Match, owner states only, and the body change versioned."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from blogwriter.articles import attach_illustrations
from blogwriter.core.testing import OWNER_OID, make_settings, principal_headers, write_headers
from blogwriter.db import Filter
from blogwriter.domain.articles import Article, ArticleVersion, Illustration
from blogwriter.domain.docstore import Stored, find_docs, read_doc, update_doc, update_singleton
from blogwriter.domain.jobtypes import ILLUSTRATIONS_GENERATE
from blogwriter.domain.settings import ModelPreferences
from blogwriter.domain.states import ArticleStatus
from blogwriter.domain.testing import SAMPLE_NOW, FakeServices, ScriptedModelRunner
from blogwriter.illustrations.steps import reencode_illustration
from blogwriter.illustrations.testing import (
    HEADINGS,
    enable_images,
    generated_png,
    save_blocklist,
    seed_article,
)
from blogwriter.storage import store_media

READ = principal_headers(OWNER_OID)
WRITE = {**READ, **write_headers(make_settings())}


def headers(etag: str) -> dict[str, str]:
    return {**WRITE, "If-Match": etag}


@pytest.fixture
async def ready(services: FakeServices) -> Stored[Article]:
    await save_blocklist(services, "Fabrikam")
    await enable_images(services)
    return await seed_article(services)


async def current(services: FakeServices, article_id: str) -> Stored[Article]:
    return await read_doc(services.store, Article, article_id)


async def with_candidates(
    services: FakeServices, article: Stored[Article], *colors: str
) -> tuple[Stored[Article], list[Illustration]]:
    """``article`` with one stored, checked candidate per colour (the first is the hero)."""
    assert services.blobs is not None
    candidates: list[Illustration] = []
    for index, color in enumerate(colors):
        data, width, height = reencode_illustration(generated_png(color), max_pixels=12_000_000)
        ref = await store_media(
            services.blobs,
            data,
            role="image",
            area="articles",
            owner_id=article.doc.id,
            content_type="image/webp",
            now=services.clock.now(),
            width=width,
            height=height,
            alt_text=f"Picture {index}",
        )
        candidates.append(
            Illustration(
                id=Illustration.id_for(ref.sha256),
                role="hero" if index == 0 else "inline",
                media=ref,
                alt_text=f"Picture {index}: glass keys on teal",
                prompt_summary="Each tool call gets its own small key",
                section=None if index == 0 else HEADINGS[1],
                job_id="illustrations-test-1",
                created_at=SAMPLE_NOW,
            )
        )
    await attach_illustrations(services.store, article.doc.id, candidates, now=services.clock.now())
    return await current(services, article.doc.id), candidates


def problem(response: httpx.Response, status: int) -> dict[str, Any]:
    assert response.status_code == status, response.text
    body: dict[str, Any] = response.json()
    return body


# --------------------------------------------------------------------------------------------
# Starting a run
# --------------------------------------------------------------------------------------------


async def test_a_run_is_named_on_the_article_then_enqueued(
    client: httpx.AsyncClient, services: FakeServices, ready: Stored[Article]
) -> None:
    body = {"count": 2, "direction": "  Warmer light, a bridge rather than a gate.  "}
    response = await client.post(
        f"/api/articles/{ready.doc.id}/images", json=body, headers=headers(ready.etag)
    )
    assert response.status_code == 202, response.text
    answer = response.json()
    job_id = answer["jobId"]
    assert job_id.startswith(f"illustrations-{ready.doc.id}-")
    assert answer["article"]["item"]["illustrationJobId"] == job_id
    assert response.headers["etag"] == answer["article"]["etag"] != ready.etag
    job = await services.jobs.get(job_id)
    assert job.type == ILLUSTRATIONS_GENERATE
    assert job.input == {
        "origin": "owner",
        "articleId": ready.doc.id,
        "count": 2,
        "direction": "Warmer light, a bridge rather than a gate.",
    }
    stored = await current(services, ready.doc.id)
    assert (stored.doc.version, stored.doc.markdown) == (ready.doc.version, ready.doc.markdown)

    again = await client.post(
        f"/api/articles/{ready.doc.id}/images",
        json={"count": 1},
        headers=headers(stored.etag),
    )
    busy = problem(again, 409)
    assert busy["code"] == "images_in_progress"
    assert busy["jobId"] == job_id


async def test_a_run_needs_a_current_etag(
    client: httpx.AsyncClient, ready: Stored[Article]
) -> None:
    path = f"/api/articles/{ready.doc.id}/images"
    assert problem(await client.post(path, json={"count": 1}, headers=WRITE), 428)
    stale = await client.post(path, json={"count": 1}, headers=headers('"stale"'))
    assert problem(stale, 412)["code"] == "etag_mismatch"


async def test_illustrations_must_be_switched_on(
    client: httpx.AsyncClient, services: FakeServices, ready: Stored[Article]
) -> None:
    await update_singleton(
        services.store,
        ModelPreferences,
        lambda prefs: prefs.model_copy(update={"images_enabled": False}),
        now=SAMPLE_NOW,
    )
    response = await client.post(
        f"/api/articles/{ready.doc.id}/images", json={"count": 1}, headers=headers(ready.etag)
    )
    assert problem(response, 409)["code"] == "images_disabled"
    assert services.jobs.enqueued == []


@pytest.mark.parametrize("ai_state", ["no_image_route", "no_ai"])
async def test_without_an_image_model_the_route_answers_503(
    client: httpx.AsyncClient,
    services: FakeServices,
    ai: ScriptedModelRunner,
    ready: Stored[Article],
    ai_state: str,
) -> None:
    if ai_state == "no_ai":
        services.ai = None
    else:
        ai.serves_images = False
    response = await client.post(
        f"/api/articles/{ready.doc.id}/images", json={"count": 1}, headers=headers(ready.etag)
    )
    body = problem(response, 503)
    assert body["code"] == "images_unavailable"
    assert response.headers["retry-after"] == "5"
    assert services.jobs.enqueued == []


async def test_without_storage_the_route_answers_503(
    client: httpx.AsyncClient, services: FakeServices, ready: Stored[Article]
) -> None:
    services.blobs = None
    response = await client.post(
        f"/api/articles/{ready.doc.id}/images", json={"count": 1}, headers=headers(ready.etag)
    )
    assert problem(response, 503)["code"] == "storage_unavailable"


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (ArticleStatus.PLANNED, "draft_missing"),
        (ArticleStatus.DRAFTING, "read_only_state"),
        (ArticleStatus.APPROVED, "read_only_state"),
        (ArticleStatus.PUBLISHED, "read_only_state"),
    ],
)
async def test_runs_start_from_review_states_only(
    client: httpx.AsyncClient, services: FakeServices, status: ArticleStatus, code: str
) -> None:
    await save_blocklist(services)
    await enable_images(services)
    article = await seed_article(services, status=status)
    response = await client.post(
        f"/api/articles/{article.doc.id}/images", json={"count": 1}, headers=headers(article.etag)
    )
    assert problem(response, 409)["code"] == code
    assert services.jobs.enqueued == []


async def test_needs_attention_may_start_a_run_but_a_title_is_required(
    client: httpx.AsyncClient, services: FakeServices
) -> None:
    await save_blocklist(services)
    await enable_images(services)
    fixing = await seed_article(services, status=ArticleStatus.NEEDS_ATTENTION)
    accepted = await client.post(
        f"/api/articles/{fixing.doc.id}/images", json={"count": 1}, headers=headers(fixing.etag)
    )
    assert accepted.status_code == 202, accepted.text
    untitled = await seed_article(services, title=None)
    response = await client.post(
        f"/api/articles/{untitled.doc.id}/images",
        json={"count": 1},
        headers=headers(untitled.etag),
    )
    body = problem(response, 409)
    assert (body["code"], body["missing"]) == ("meta_incomplete", ["title"])


async def test_the_blocklist_screens_the_direction_and_the_metadata(
    client: httpx.AsyncClient, services: FakeServices, ready: Stored[Article]
) -> None:
    response = await client.post(
        f"/api/articles/{ready.doc.id}/images",
        json={"count": 1, "direction": "Show the Fabrikam data centre."},
        headers=headers(ready.etag),
    )
    body = problem(response, 422)
    assert body["code"] == "confidential_text"
    assert [finding["field"] for finding in body["findings"]] == ["direction"]
    assert "Fabrikam" not in response.text  # term ids only, never the term
    assert services.jobs.enqueued == []


async def test_an_unsaved_blocklist_refuses_every_run(
    client: httpx.AsyncClient, services: FakeServices
) -> None:
    await enable_images(services)
    article = await seed_article(services)
    response = await client.post(
        f"/api/articles/{article.doc.id}/images", json={"count": 1}, headers=headers(article.etag)
    )
    assert problem(response, 409)["code"] == "blocklist_not_configured"


@pytest.mark.parametrize(
    "body", [{"count": 0}, {"count": 4}, {"count": 1, "direction": "x" * 301}, {"extra": 1}]
)
async def test_bodies_are_validated(
    client: httpx.AsyncClient, ready: Stored[Article], body: dict[str, object]
) -> None:
    response = await client.post(
        f"/api/articles/{ready.doc.id}/images", json=body, headers=headers(ready.etag)
    )
    assert problem(response, 422)["code"] == "validation_failed"


# --------------------------------------------------------------------------------------------
# Owner actions on candidates
# --------------------------------------------------------------------------------------------


async def test_a_candidate_becomes_the_hero_and_back_to_the_card(
    client: httpx.AsyncClient, services: FakeServices, ready: Stored[Article]
) -> None:
    article, (hero, _) = await with_candidates(services, ready, "#335577", "#446688")
    card = next(item for item in article.doc.media if item.role == "og_image")
    response = await client.post(
        f"/api/articles/{article.doc.id}/images/{hero.id}/hero", headers=headers(article.etag)
    )
    assert response.status_code == 200, response.text
    chosen = await current(services, article.doc.id)
    heroes = [item for item in chosen.doc.media if item.role == "hero_image"]
    assert [(item.name, item.alt_text) for item in heroes] == [(hero.media.name, hero.alt_text)]
    assert card in chosen.doc.media  # og:image stays the typographic card
    assert chosen.doc.version == article.doc.version  # not content: no new version
    assert response.json()["etag"] == chosen.etag
    same = await client.post(
        f"/api/articles/{article.doc.id}/images/{hero.id}/hero", headers=headers(chosen.etag)
    )
    assert same.status_code == 200
    assert same.json()["etag"] == chosen.etag  # choosing it again writes nothing

    cleared = await client.delete(
        f"/api/articles/{article.doc.id}/hero", headers=headers(chosen.etag)
    )
    assert cleared.status_code == 200, cleared.text
    back = await current(services, article.doc.id)
    assert back.doc.hero_media() is None  # the publish job uses the card again
    assert card in back.doc.media


async def test_the_hero_route_refuses_stale_unknown_and_read_only(
    client: httpx.AsyncClient, services: FakeServices, ready: Stored[Article]
) -> None:
    article, (hero,) = await with_candidates(services, ready, "#335577")
    path = f"/api/articles/{article.doc.id}/images/{hero.id}/hero"
    assert problem(await client.post(path, headers=headers(ready.etag)), 412)
    unknown = f"/api/articles/{article.doc.id}/images/ill-0000000000000000/hero"
    assert problem(await client.post(unknown, headers=headers(article.etag)), 404)["code"] == (
        "not_found"
    )
    approved = await update_doc(
        services.store,
        Article,
        article.doc.id,
        lambda doc: doc.model_copy(update={"status": ArticleStatus.APPROVED}),
    )
    body = problem(await client.post(path, headers=headers(approved.etag)), 409)
    assert (body["code"], body["state"]) == ("read_only_state", "approved")


async def test_inserting_an_image_after_a_heading_is_a_versioned_body_change(
    client: httpx.AsyncClient, services: FakeServices, ready: Stored[Article]
) -> None:
    article, (_, inline) = await with_candidates(services, ready, "#335577", "#446688")
    response = await client.post(
        f"/api/articles/{article.doc.id}/images/{inline.id}/insert",
        json={"heading": "the GATEWAY pattern"},
        headers=headers(article.etag),
    )
    assert response.status_code == 200, response.text
    saved = response.json()
    assert saved["findings"] == []
    after = await current(services, article.doc.id)
    image = f"![Picture 1: glass keys on teal](/api/media/{inline.media.name})"
    assert f"## {HEADINGS[1]}\n\n{image}\n\n" in after.doc.markdown
    assert after.doc.version == article.doc.version + 1
    assert after.doc.content_sha256 == after.doc.compute_sha256() != article.doc.compute_sha256()
    assert saved["etag"] == after.etag == response.headers["etag"]
    versions = await find_docs(
        services.store,
        ArticleVersion,
        [Filter("articleId", "==", article.doc.id)],
        partition_key=article.doc.id,
    )
    assert after.doc.version in {stored.doc.version for stored in versions}

    twice = await client.post(
        f"/api/articles/{article.doc.id}/images/{inline.id}/insert",
        json={"heading": HEADINGS[2]},
        headers=headers(after.etag),
    )
    assert problem(twice, 409)["code"] == "illustration_inserted"


async def test_inserting_needs_a_real_heading(
    client: httpx.AsyncClient, services: FakeServices, ready: Stored[Article]
) -> None:
    article, (hero,) = await with_candidates(services, ready, "#335577")
    response = await client.post(
        f"/api/articles/{article.doc.id}/images/{hero.id}/insert",
        json={"heading": "A heading that is not there"},
        headers=headers(article.etag),
    )
    assert problem(response, 422)["code"] == "heading_not_found"
    assert (await current(services, article.doc.id)).doc.version == article.doc.version


async def test_alt_text_follows_the_image_to_the_hero_and_the_body(
    client: httpx.AsyncClient, services: FakeServices, ready: Stored[Article]
) -> None:
    article, (hero, inline) = await with_candidates(services, ready, "#335577", "#446688")
    chose = await client.post(
        f"/api/articles/{article.doc.id}/images/{hero.id}/hero", headers=headers(article.etag)
    )
    inserted = await client.post(
        f"/api/articles/{article.doc.id}/images/{inline.id}/insert",
        json={"heading": HEADINGS[0]},
        headers=headers(chose.json()["etag"]),
    )
    etag = inserted.json()["etag"]
    version = inserted.json()["item"]["version"]

    hero_alt = await client.put(
        f"/api/articles/{article.doc.id}/images/{hero.id}/alt",
        json={"altText": "  Small glass keys,\neach fitted to one door.  "},
        headers=headers(etag),
    )
    assert hero_alt.status_code == 200, hero_alt.text
    doc = (await current(services, article.doc.id)).doc
    hero_media = doc.hero_media()
    assert hero_media is not None
    assert hero_media.alt_text == "Small glass keys, each fitted to one door."
    assert doc.version == version  # the hero is not in the body: no new version

    inline_alt = await client.put(
        f"/api/articles/{article.doc.id}/images/{inline.id}/alt",
        json={"altText": "A narrow beam [of light] on one step"},
        headers=headers(hero_alt.json()["etag"]),
    )
    assert inline_alt.status_code == 200, inline_alt.text
    doc = (await current(services, article.doc.id)).doc
    assert doc.version == version + 1
    assert (
        f"![A narrow beam &#91;of light&#93; on one step](/api/media/{inline.media.name})"
        in doc.markdown
    )
    edited = doc.illustration(inline.id)
    assert edited is not None
    assert edited.alt_text == "A narrow beam [of light] on one step"


async def test_alt_text_is_screened_and_must_say_something(
    client: httpx.AsyncClient, services: FakeServices, ready: Stored[Article]
) -> None:
    article, (hero,) = await with_candidates(services, ready, "#335577")
    path = f"/api/articles/{article.doc.id}/images/{hero.id}/alt"
    blocked = await client.put(
        path, json={"altText": "The Fabrikam lobby"}, headers=headers(article.etag)
    )
    assert problem(blocked, 422)["code"] == "confidential_text"
    blank = await client.put(path, json={"altText": " \n "}, headers=headers(article.etag))
    assert problem(blank, 422)["code"] == "invalid_alt_text"


async def test_only_unused_candidates_can_be_discarded(
    client: httpx.AsyncClient, services: FakeServices, ready: Stored[Article]
) -> None:
    article, (hero, inline, spare) = await with_candidates(
        services, ready, "#335577", "#446688", "#557799"
    )
    chose = await client.post(
        f"/api/articles/{article.doc.id}/images/{hero.id}/hero", headers=headers(article.etag)
    )
    inserted = await client.post(
        f"/api/articles/{article.doc.id}/images/{inline.id}/insert",
        json={"heading": HEADINGS[0]},
        headers=headers(chose.json()["etag"]),
    )
    etag = inserted.json()["etag"]
    for used in (hero, inline):
        refused = await client.delete(
            f"/api/articles/{article.doc.id}/images/{used.id}", headers=headers(etag)
        )
        assert problem(refused, 409)["code"] == "illustration_in_use"
    discarded = await client.delete(
        f"/api/articles/{article.doc.id}/images/{spare.id}", headers=headers(etag)
    )
    assert discarded.status_code == 200, discarded.text
    doc = (await current(services, article.doc.id)).doc
    assert [item.id for item in doc.illustrations] == [hero.id, inline.id]
    assert services.blobs is not None
    assert ("media", spare.media.name) in services.blobs.blobs  # blobs stay (no GC here)
    gone = await client.delete(
        f"/api/articles/{article.doc.id}/images/{spare.id}",
        headers=headers(discarded.json()["etag"]),
    )
    assert problem(gone, 404)["code"] == "not_found"


async def test_candidates_are_capped_and_attach_is_idempotent(
    services: FakeServices, ready: Stored[Article]
) -> None:
    colors = [f"#{index:02x}5577" for index in range(14)]
    article, candidates = await with_candidates(services, ready, *colors[:12])
    assert len(article.doc.illustrations) == 12
    again = await attach_illustrations(
        services.store, article.doc.id, candidates, now=services.clock.now()
    )
    assert again.etag == article.etag  # nothing new: nothing written
    more, extra = await with_candidates(services, article, *colors[12:])
    del extra
    assert len(more.doc.illustrations) == 12
    assert more.doc.illustrations[0].id == candidates[2].id  # the oldest unused went first


# --------------------------------------------------------------------------------------------
# Owner only, and CSRF on every write
# --------------------------------------------------------------------------------------------


def new_routes(article_id: str, illustration_id: str) -> list[tuple[str, str, Any]]:
    """Every illustrations route: method, path and a valid body."""
    item = f"/api/articles/{article_id}/images/{illustration_id}"
    return [
        ("POST", f"/api/articles/{article_id}/images", {"count": 1}),
        ("POST", f"{item}/hero", None),
        ("DELETE", f"/api/articles/{article_id}/hero", None),
        ("POST", f"{item}/insert", {"heading": HEADINGS[0]}),
        ("PUT", f"{item}/alt", {"altText": "Small glass keys in a row."}),
        ("DELETE", item, None),
    ]


async def test_every_route_is_owner_only_and_every_write_needs_csrf(
    client: httpx.AsyncClient, services: FakeServices, ready: Stored[Article]
) -> None:
    article, (candidate,) = await with_candidates(services, ready, "#335577")
    routes = new_routes(article.doc.id, candidate.id)
    assert len(routes) == 6
    stranger = {
        **principal_headers("00000000-0000-0000-0000-00000000beef"),
        **write_headers(make_settings()),
        "If-Match": article.etag,
    }
    no_csrf = {**READ, "If-Match": article.etag}  # the owner, without the app cookie
    cross_site = {**WRITE, "Sec-Fetch-Site": "cross-site", "If-Match": article.etag}
    for method, path, body in routes:
        refused = await client.request(method, path, json=body, headers=stranger)
        assert problem(refused, 403)["code"] == "not_owner", (method, path)
        for bad in (no_csrf, cross_site):
            forged = await client.request(method, path, json=body, headers=bad)
            assert problem(forged, 403)["code"] == "csrf_failed", (method, path)
    after = await current(services, article.doc.id)
    assert after.etag == article.etag  # nothing was written
    assert services.jobs.enqueued == []
