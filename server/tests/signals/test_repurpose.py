"""Repurposing (plan: "Post lanes"): a post that went out becomes a re-cut idea or a flagship
candidate (``origin: repurpose``, ``repurposeOf``), once per kind; the ``repurpose`` post lane
starts from such an idea."""

from __future__ import annotations

from typing import Any, Final

import httpx

from blogwriter.core.testing import OWNER_OID, make_settings, principal_headers, write_headers
from blogwriter.domain.docstore import upsert_doc
from blogwriter.domain.posts import Post
from blogwriter.domain.settings import Blocklist, BlocklistTerm
from blogwriter.domain.states import PostStatus
from blogwriter.domain.testing import SAMPLE_NOW, FakeServices, sample_document
from blogwriter.signals import repurpose_idea_id

READ: Final = principal_headers(OWNER_OID)
WRITE: Final = {**READ, **write_headers(make_settings())}


async def posted(services: FakeServices, **fields: object) -> Post:
    values: dict[str, object] = {
        "status": PostStatus.POSTED,
        "lane": "native",
        "pillar": "agents",
        "format": "text",
        "display_text": "Scope every agent token to one tool call.\n\nHere is how we did it.",
        "posted_at": "2026-09-10T14:00:00.000000Z",
        **fields,
    }
    post = sample_document(Post, **values)
    await upsert_doc(services.store, post)
    return post


async def save_blocklist(services: FakeServices, *terms: str) -> None:
    await upsert_doc(
        services.store,
        Blocklist(
            id=Blocklist.SINGLETON_ID,
            created_at=SAMPLE_NOW,
            updated_at=SAMPLE_NOW,
            configured=True,
            terms=[
                BlocklistTerm(id=f"t-{index + 1}", term=term, category="client")
                for index, term in enumerate(terms)
            ],
        ),
    )


async def repurpose(client: httpx.AsyncClient, post_id: str, kind: str = "recut") -> httpx.Response:
    return await client.post(
        "/api/ideas/repurpose", json={"postId": post_id, "kind": kind}, headers=WRITE
    )


async def test_a_posted_post_becomes_a_repurpose_idea_once(
    client: httpx.AsyncClient, services: FakeServices
) -> None:
    await save_blocklist(services)
    post = await posted(services)
    first = await repurpose(client, post.id)
    assert first.status_code == 201, first.text
    idea: dict[str, Any] = first.json()["item"]
    assert idea["id"] == repurpose_idea_id(post.id, "recut")
    assert (idea["origin"], idea["repurposeOf"], idea["status"]) == ("repurpose", post.id, "new")
    assert (idea["suggestedLane"], idea["suggestedFormat"], idea["pillar"]) == (
        "repurpose",
        "text",
        "agents",
    )
    assert idea["title"] == "Re-cut: Scope every agent token to one tool call."
    again = await repurpose(client, post.id)
    assert (again.status_code, again.json()["item"]["id"]) == (200, idea["id"])
    flagship = await repurpose(client, post.id, "flagship")
    assert flagship.status_code == 201
    assert flagship.json()["item"]["suggestedLane"] == "flagship"
    assert flagship.json()["item"]["suggestedFormat"] is None


async def test_only_a_post_that_went_out_is_repurposed(
    client: httpx.AsyncClient, services: FakeServices
) -> None:
    await save_blocklist(services)
    draft = await posted(services, status=PostStatus.DRAFT, posted_at=None)
    response = await repurpose(client, draft.id)
    assert (response.status_code, response.json()["code"]) == (409, "not_posted")
    missing = await repurpose(client, "post-0193f000000000000000000000ff")
    assert missing.status_code == 404


async def test_a_blocklisted_first_line_is_never_quoted(
    client: httpx.AsyncClient, services: FakeServices
) -> None:
    await save_blocklist(services, "agent token")
    post = await posted(services)
    response = await repurpose(client, post.id)
    assert response.status_code == 201
    title = response.json()["item"]["title"]
    assert "agent token" not in title.lower()
    assert title.startswith("Re-cut your post of 2026-09-10")
