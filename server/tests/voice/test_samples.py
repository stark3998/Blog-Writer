"""The sample routes: scoring on save, the blocklist, owner URLs through the fetcher only, CAS."""

from __future__ import annotations

from collections.abc import Collection
from typing import override

import httpx
import pytest

from blogwriter.domain.base import sha256_hex
from blogwriter.domain.docstore import create_doc, read_doc
from blogwriter.domain.ports import FetchedPage
from blogwriter.domain.stories import StoryEntry
from blogwriter.domain.testing import FakeServices, StaticPageFetcher, sample_document
from blogwriter.domain.voice import VoiceSample
from blogwriter.voice.testing import LINKEDIN_POSTS, MarkerSlopChecker, seed_policy

CURLY = "\u2019"
PAGE_URL = "https://writer.example/blog/agent-identity"


class RecordingFetcher(StaticPageFetcher):
    """The fake fetcher, also recording the allow list of every call."""

    def __init__(self, pages: dict[str, str], *, title: str = "Agent identity") -> None:
        super().__init__(pages, title=title)
        self.calls: list[tuple[str, list[str]]] = []

    @override
    async def fetch(self, url: str, *, allowed_urls: Collection[str]) -> FetchedPage:
        self.calls.append((url, list(allowed_urls)))
        return await super().fetch(url, allowed_urls=allowed_urls)


async def create(
    client: httpx.AsyncClient, headers: dict[str, str], **body: object
) -> httpx.Response:
    return await client.post("/api/voice/samples", json=body, headers=headers)


async def test_a_new_sample_is_scored_and_stored(
    services: FakeServices, client: httpx.AsyncClient, write_headers: dict[str, str]
) -> None:
    await seed_policy(services)
    flagged = await create(
        client,
        write_headers,
        sourceType="linkedin_post",
        text=f"Let{CURLY}s dive in.\r\n\r\nWe moved 2,000 apps in 14 months.",
        ownerUnassisted=True,
        title="Migration notes",
        pillar="identity",
    )
    assert flagged.status_code == 201, flagged.text
    body = flagged.json()
    item = body["item"]
    assert flagged.headers["ETag"] == body["etag"]
    assert item["text"] == f"Let{CURLY}s dive in.\n\nWe moved 2,000 apps in 14 months."
    assert [flag["code"] for flag in item["flags"]] == ["ai-tell"]
    assert item["flags"][0]["start"] == 0
    assert item["exemplarEligible"] is False
    assert item["aiTellScore"] > 0
    assert item["weight"] == 4.0
    assert item["contentSha256"] == sha256_hex(item["text"])
    assert item["status"] == "active"
    clean = await create(client, write_headers, sourceType="linkedin_post", text=LINKEDIN_POSTS[0])
    assert clean.status_code == 201
    assert clean.json()["item"]["flags"] == []
    assert clean.json()["item"]["exemplarEligible"] is True


async def test_structural_findings_are_flags_too(
    services: FakeServices, client: httpx.AsyncClient, write_headers: dict[str, str]
) -> None:
    await seed_policy(services)
    services.slop = MarkerSlopChecker({"one audience, five minutes": "rule-of-three"})
    flagged = await create(
        client, write_headers, sourceType="linkedin_post", text=LINKEDIN_POSTS[1]
    )
    item = flagged.json()["item"]
    assert [flag["code"] for flag in item["flags"]] == ["rule-of-three"]
    assert item["exemplarEligible"] is False
    services.slop = None
    unchecked = await create(
        client, write_headers, sourceType="portfolio_post", text=LINKEDIN_POSTS[2]
    )
    assert unchecked.json()["item"]["flags"] == []
    assert unchecked.json()["item"]["exemplarEligible"] is False  # not checked: fail closed


async def test_text_or_url_is_required(
    services: FakeServices, client: httpx.AsyncClient, write_headers: dict[str, str]
) -> None:
    await seed_policy(services)
    for body in ({"sourceType": "linkedin_post"}, {"sourceType": "linkedin_post", "text": "  \n"}):
        response = await client.post("/api/voice/samples", json=body, headers=write_headers)
        assert response.status_code == 422
        assert response.json()["code"] == "text_or_url_required"


async def test_pipeline_sources_are_not_owner_input(
    services: FakeServices, client: httpx.AsyncClient, write_headers: dict[str, str]
) -> None:
    await seed_policy(services)
    for source in ("interview_answer", "final_post"):
        response = await create(client, write_headers, sourceType=source, text="An answer.")
        assert response.status_code == 422
        assert response.json()["code"] == "validation_failed"


async def test_the_blocklist_refuses_without_echoing_the_term(
    services: FakeServices, client: httpx.AsyncClient, write_headers: dict[str, str]
) -> None:
    await seed_policy(services, terms=["Contoso"])
    response = await create(
        client,
        write_headers,
        sourceType="linkedin_post",
        text="We moved Contoso off the old provider.",
        title="Contoso notes",
    )
    assert response.status_code == 422
    problem = response.json()
    assert problem["code"] == "confidential_text"
    fields = sorted({finding["field"] for finding in problem["findings"]})
    assert fields == ["text", "title"]
    assert all(finding["ref"] == "t-1" for finding in problem["findings"])
    assert "Contoso" not in response.text
    assert await services.store.find("stories", []) == []


async def test_an_unsaved_blocklist_refuses_every_sample(
    services: FakeServices, client: httpx.AsyncClient, write_headers: dict[str, str]
) -> None:
    await seed_policy(services, blocklist_configured=False)
    response = await create(client, write_headers, sourceType="linkedin_post", text="Hello there.")
    assert response.status_code == 409
    assert response.json()["code"] == "blocklist_not_configured"


async def test_owner_urls_are_fetched_only_through_the_fetcher(
    services: FakeServices, client: httpx.AsyncClient, write_headers: dict[str, str]
) -> None:
    await seed_policy(services)
    fetcher = RecordingFetcher({PAGE_URL: LINKEDIN_POSTS[4]})
    services.fetcher = fetcher
    response = await create(client, write_headers, sourceType="portfolio_post", url=PAGE_URL)
    assert response.status_code == 201, response.text
    item = response.json()["item"]
    assert fetcher.calls == [(PAGE_URL, [PAGE_URL])]
    assert item["text"] == LINKEDIN_POSTS[4]
    assert item["url"] == PAGE_URL
    assert item["title"] == "Agent identity"
    assert services.egress.requested == []


async def test_pasted_text_is_never_fetched(
    services: FakeServices, client: httpx.AsyncClient, write_headers: dict[str, str]
) -> None:
    await seed_policy(services)
    fetcher = RecordingFetcher({PAGE_URL: "other"})
    services.fetcher = fetcher
    response = await create(
        client, write_headers, sourceType="portfolio_post", url=PAGE_URL, text=LINKEDIN_POSTS[2]
    )
    assert response.status_code == 201
    assert fetcher.calls == []
    assert response.json()["item"]["text"] == LINKEDIN_POSTS[2]


async def test_fetch_failures_and_a_missing_fetcher(
    services: FakeServices, client: httpx.AsyncClient, write_headers: dict[str, str]
) -> None:
    await seed_policy(services)
    services.fetcher = RecordingFetcher({})
    failed = await create(client, write_headers, sourceType="portfolio_post", url=PAGE_URL)
    assert failed.status_code == 502
    assert failed.json()["code"] == "fetch_failed"
    assert PAGE_URL not in failed.text
    services.fetcher = None
    missing = await create(client, write_headers, sourceType="portfolio_post", url=PAGE_URL)
    assert missing.status_code == 503
    assert missing.json()["code"] == "fetcher_unavailable"


async def test_a_blocklisted_url_is_never_fetched(
    services: FakeServices, client: httpx.AsyncClient, write_headers: dict[str, str]
) -> None:
    await seed_policy(services, terms=["contoso"])
    fetcher = RecordingFetcher({"https://contoso.example/post": "text"})
    services.fetcher = fetcher
    response = await create(
        client, write_headers, sourceType="portfolio_post", url="https://contoso.example/post"
    )
    assert response.status_code == 422
    assert response.json()["findings"][0]["field"] == "url"
    assert fetcher.calls == []


async def test_fetched_text_passes_the_blocklist_too(
    services: FakeServices, client: httpx.AsyncClient, write_headers: dict[str, str]
) -> None:
    await seed_policy(services, terms=["Fabrikam"])
    services.fetcher = RecordingFetcher({PAGE_URL: "Our Fabrikam rollout took a year."})
    response = await create(client, write_headers, sourceType="portfolio_post", url=PAGE_URL)
    assert response.status_code == 422
    assert "Fabrikam" not in response.text
    assert await services.store.find("stories", []) == []


async def test_updates_need_the_current_etag_and_rescore(
    services: FakeServices,
    client: httpx.AsyncClient,
    write_headers: dict[str, str],
    read_headers: dict[str, str],
) -> None:
    await seed_policy(services)
    created = (
        await create(client, write_headers, sourceType="linkedin_post", text=LINKEDIN_POSTS[0])
    ).json()
    sample_id, etag = created["item"]["id"], created["etag"]
    path = f"/api/voice/samples/{sample_id}"
    missing = await client.put(path, json={"text": "x"}, headers=write_headers)
    assert missing.status_code == 428
    stale = await client.put(
        path, json={"text": "x"}, headers={**write_headers, "If-Match": '"stale"'}
    )
    assert stale.status_code == 412
    assert stale.json()["current"]["etag"] == etag
    edited = await client.put(
        path,
        json={"text": "We leveraged the old gateway for a year.", "ownerUnassisted": True},
        headers={**write_headers, "If-Match": etag},
    )
    assert edited.status_code == 200, edited.text
    item = edited.json()["item"]
    assert [flag["code"] for flag in item["flags"]] == ["ai-tell"]
    assert item["exemplarEligible"] is False
    assert item["contentSha256"] == sha256_hex("We leveraged the old gateway for a year.")
    assert item["weight"] == 4.0
    assert edited.headers["ETag"] != etag
    fetched = await client.get(path, headers=read_headers)
    assert fetched.json()["etag"] == edited.json()["etag"]


async def test_null_clears_and_omitted_fields_stay(
    services: FakeServices, client: httpx.AsyncClient, write_headers: dict[str, str]
) -> None:
    await seed_policy(services)
    created = (
        await create(
            client,
            write_headers,
            sourceType="linkedin_post",
            text=LINKEDIN_POSTS[1],
            title="Tokens",
            pillar="agents",
        )
    ).json()
    response = await client.put(
        f"/api/voice/samples/{created['item']['id']}",
        json={"title": None},
        headers={**write_headers, "If-Match": created["etag"]},
    )
    item = response.json()["item"]
    assert item["title"] is None
    assert item["pillar"] == "agents"
    assert item["text"] == LINKEDIN_POSTS[1]


async def test_an_edit_must_clear_the_blocklist(
    services: FakeServices, client: httpx.AsyncClient, write_headers: dict[str, str]
) -> None:
    await seed_policy(services, terms=["Contoso"])
    created = (
        await create(client, write_headers, sourceType="linkedin_post", text=LINKEDIN_POSTS[2])
    ).json()
    response = await client.put(
        f"/api/voice/samples/{created['item']['id']}",
        json={"text": "Contoso asked for it."},
        headers={**write_headers, "If-Match": created["etag"]},
    )
    assert response.status_code == 422
    assert "Contoso" not in response.text
    stored = await read_doc(services.store, VoiceSample, created["item"]["id"])
    assert stored.doc.text == LINKEDIN_POSTS[2]


async def test_status_moves_through_its_machine(
    services: FakeServices, client: httpx.AsyncClient, write_headers: dict[str, str]
) -> None:
    await seed_policy(services)
    created = (
        await create(client, write_headers, sourceType="linkedin_post", text=LINKEDIN_POSTS[3])
    ).json()
    path = f"/api/voice/samples/{created['item']['id']}/status"
    excluded = await client.post(
        path, json={"status": "excluded"}, headers={**write_headers, "If-Match": created["etag"]}
    )
    assert excluded.status_code == 200
    assert excluded.json()["item"]["status"] == "excluded"
    again = await client.post(
        path,
        json={"status": "excluded"},
        headers={**write_headers, "If-Match": excluded.json()["etag"]},
    )
    assert again.status_code == 409
    assert again.json()["code"] == "invalid_transition"
    back = await client.post(
        path,
        json={"status": "active"},
        headers={**write_headers, "If-Match": excluded.json()["etag"]},
    )
    assert back.json()["item"]["status"] == "active"


async def test_delete_needs_the_etag_and_never_touches_other_kinds(
    services: FakeServices,
    client: httpx.AsyncClient,
    write_headers: dict[str, str],
    read_headers: dict[str, str],
) -> None:
    await seed_policy(services)
    story = await create_doc(services.store, sample_document(StoryEntry))
    wrong_kind = await client.delete(
        f"/api/voice/samples/{story.doc.id}", headers={**write_headers, "If-Match": story.etag}
    )
    assert wrong_kind.status_code == 404
    assert (await read_doc(services.store, StoryEntry, story.doc.id)).doc.id == story.doc.id
    created = (
        await create(client, write_headers, sourceType="linkedin_post", text=LINKEDIN_POSTS[5])
    ).json()
    path = f"/api/voice/samples/{created['item']['id']}"
    stale = await client.delete(path, headers={**write_headers, "If-Match": '"stale"'})
    assert stale.status_code == 412
    deleted = await client.delete(path, headers={**write_headers, "If-Match": created["etag"]})
    assert deleted.status_code == 204
    assert (await client.get(path, headers=read_headers)).status_code == 404


async def test_lists_filter_and_page(
    services: FakeServices,
    client: httpx.AsyncClient,
    write_headers: dict[str, str],
    read_headers: dict[str, str],
) -> None:
    await seed_policy(services)
    for index, text in enumerate(LINKEDIN_POSTS[:3]):
        services.clock.advance(1)
        source = "linkedin_comment" if index == 0 else "linkedin_post"
        assert (await create(client, write_headers, sourceType=source, text=text)).is_success
    posts = await client.get(
        "/api/voice/samples", params={"sourceType": "linkedin_post"}, headers=read_headers
    )
    assert [entry["item"]["text"] for entry in posts.json()["items"]] == [
        LINKEDIN_POSTS[2],
        LINKEDIN_POSTS[1],
    ]
    first = await client.get("/api/voice/samples", params={"limit": 2}, headers=read_headers)
    page = first.json()
    assert len(page["items"]) == 2
    assert page["nextCursor"] is not None
    second = await client.get(
        "/api/voice/samples",
        params={"limit": 2, "cursor": page["nextCursor"]},
        headers=read_headers,
    )
    assert [entry["item"]["text"] for entry in second.json()["items"]] == [LINKEDIN_POSTS[0]]
    assert second.json()["nextCursor"] is None
    excluded = await client.get(
        "/api/voice/samples", params={"status": "excluded"}, headers=read_headers
    )
    assert excluded.json()["items"] == []
    broken = await client.get("/api/voice/samples", params={"cursor": "%%%"}, headers=read_headers)
    assert broken.status_code == 422
    assert broken.json()["code"] == "invalid_cursor"


@pytest.mark.parametrize("path", ["/api/voice/samples/a#b", "/api/voice/samples/missing"])
async def test_unknown_samples_are_404(
    client: httpx.AsyncClient, read_headers: dict[str, str], path: str
) -> None:
    response = await client.get(path, headers=read_headers)
    assert response.status_code == 404
