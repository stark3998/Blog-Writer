"""The posts list reads projections (contract I-10): ``PostRow`` mirrors ``Post``, computes the
same hook and approval state, and the route never asks the store for hook variants, checks,
served-by records, snapshots, the carousel spec or the final text."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Final, override

import pytest
from pydantic import BaseModel

from blogwriter.core.testing import OWNER_OID, asgi_client, make_settings, principal_headers
from blogwriter.db import Doc, Filter, MemoryStore, OrderBy
from blogwriter.domain.common import MediaRef
from blogwriter.domain.docstore import create_doc, find_docs
from blogwriter.domain.posts import Approval, KitInfo, LinkException, Post, Slot
from blogwriter.domain.states import KitState, PostStatus
from blogwriter.domain.testing import SAMPLE_NOW, make_app, make_services, sample_document
from blogwriter.jobs.testing import DelegatingStore, FakeClock
from blogwriter.linkedin.posts import get_router
from blogwriter.linkedin.posts._post import first_line
from blogwriter.linkedin.posts.models import PostRow
from blogwriter.linkedin.posts.serialize import serialize
from blogwriter.linkedin.posts.testing import T0

READ: Final = principal_headers(OWNER_OID)
LARGE: Final = frozenset(
    {
        "hookVariants",
        "aiDraftText",
        "finalText",
        "carouselSpec",
        "lint",
        "derivedClaims",
        "nearDuplicate",
        "servedBy",
        "snapshots",
        "intent",
        "staging",
    }
)
PDF: Final = MediaRef(
    role="carousel_pdf",
    name="posts/p/" + "a" * 64 + ".pdf",
    sha256="a" * 64,
    content_type="application/pdf",
    size_bytes=100,
    alt_text="Slides",
)


class ProjectionLog(DelegatingStore):
    """A MemoryStore that records every query's projection."""

    def __init__(self) -> None:
        super().__init__(MemoryStore())
        self.finds: list[tuple[str, tuple[str, ...] | None]] = []

    @override
    async def find(
        self,
        container: str,
        filters: Sequence[Filter] = (),
        *,
        order_by: OrderBy | None = None,
        limit: int | None = None,
        partition_key: str | None = None,
        fields: Sequence[str] | None = None,
    ) -> list[Doc]:
        self.finds.append((container, None if fields is None else tuple(fields)))
        return await super().find(
            container,
            filters,
            order_by=order_by,
            limit=limit,
            partition_key=partition_key,
            fields=fields,
        )


def assert_mirrors(row: type[BaseModel], document: type[BaseModel]) -> None:
    """Every row field is the document's field: same name, alias, type, rules and default."""
    for name, field in row.model_fields.items():
        source = document.model_fields[name]
        assert field.alias == source.alias, name
        assert field.annotation == source.annotation, name
        assert field.metadata == source.metadata, name
        assert field.is_required() == source.is_required(), name
        if not field.is_required():
            assert field.get_default(call_default_factory=True) == source.get_default(
                call_default_factory=True
            ), name


def text_post(**fields: object) -> Post:
    display, payload = serialize("\n\nWe moved 2,000 apps in 14 months.\n\nHere is how.", [])
    values: dict[str, object] = {
        "display_text": display,
        "payload": payload,
        "ai_draft_text": "The model's first try.",
        "final_text": None,
        **fields,
    }
    return sample_document(Post, **values)


def approved(post: Post) -> Post:
    return post.model_copy(
        update={"approval": Approval(sha256=post.compute_approval_hash(), approved_at=SAMPLE_NOW)}
    )


def test_post_rows_mirror_posts() -> None:
    assert_mirrors(PostRow, Post)
    assert not LARGE & {field.alias for field in PostRow.model_fields.values()}


EDITS: Final[dict[str, Callable[[Post], Post]]] = {
    "unchanged": lambda post: post,
    "text": lambda post: post.model_copy(update={"display_text": post.display_text + " More."}),
    "payload": lambda post: post.model_copy(update={"payload": post.payload + "!"}),
    "title": lambda post: post.model_copy(update={"document_title": "Another deck"}),
    "media": lambda post: post.model_copy(update={"media": []}),
    "format": lambda post: post.model_copy(update={"format": "image_card"}),
    "poll": lambda post: post.model_copy(update={"poll": {"question": "Which?"}}),
    "link": lambda post: post.model_copy(
        update={
            "link_exception": LinkException(
                url="https://example.com/other", reason="Experiment", approved_at=SAMPLE_NOW
            )
        }
    ),
}


@pytest.mark.parametrize("edit", sorted(EDITS))
async def test_rows_compute_the_hook_and_the_approval_state_like_posts(edit: str) -> None:
    store = MemoryStore()
    post = approved(
        text_post(
            format="carousel",
            document_title="Deck",
            media=[PDF],
            link_exception=LinkException(
                url="https://example.com/a", reason="Experiment", approved_at=SAMPLE_NOW
            ),
        )
    )
    changed = EDITS[edit](post)
    await create_doc(store, changed)
    (row,) = await find_docs(store, Post, fields=PostRow)
    assert row.doc.approval_current is changed.approval_current
    assert row.doc.approval_current is (edit == "unchanged")
    assert row.doc.compute_approval_hash() == changed.compute_approval_hash()
    assert first_line(row.doc.display_text) == "We moved 2,000 apps in 14 months."


async def test_the_list_never_asks_for_large_fields() -> None:
    log = ProjectionLog()
    services = make_services(clock=FakeClock(T0), store=log)
    kit = approved(text_post(id="post-0193f000000000000000000000a1")).model_copy(
        update={
            "status": PostStatus.KIT,
            "go_live_mode": "kit",
            "kit": KitInfo(state=KitState.PREPARED, prepared_at=SAMPLE_NOW),
            "slot": Slot.at("2026-12-01", "10:00", "America/New_York"),
            "updated_at": "2026-11-26T12:00:00.000002Z",
        }
    )
    draft = text_post(id="post-0193f000000000000000000000a2", review_only=True)
    for post in (kit, draft):
        await create_doc(log, post)
    log.finds.clear()
    app = make_app(services, routers=[get_router()], settings=make_settings())
    async with asgi_client(app) as client:
        response = await client.get("/api/posts", headers=READ)
    assert response.status_code == 200, response.text
    assert log.finds == [("posts", ("kind", *(f.alias for f in PostRow.model_fields.values())))]
    items = {entry["item"]["id"]: entry for entry in response.json()["items"]}
    stored = {s.doc.id: s for s in await find_docs(log, Post)}
    for post in (kit, draft):
        whole = stored[post.id]
        entry = items[post.id]
        assert entry["etag"] == whole.etag
        assert entry["item"] == {
            "id": post.id,
            "status": whole.doc.status.value,
            "statusChangedAt": whole.doc.status_changed_at,
            "lane": whole.doc.lane,
            "pillar": whole.doc.pillar,
            "format": whole.doc.format,
            "hook": first_line(whole.doc.display_text),
            "slot": None
            if whole.doc.slot is None
            else whole.doc.slot.model_dump(mode="json", by_alias=True),
            "goLiveMode": whole.doc.go_live_mode,
            "kitState": None if whole.doc.kit is None else whole.doc.kit.state.value,
            "approvalCurrent": whole.doc.approval_current,
            "postedAt": whole.doc.posted_at,
            "reviewOnly": whole.doc.review_only,
            "articleId": whole.doc.article_id,
            "weekPlanId": whole.doc.week_plan_id,
            "planSlotId": whole.doc.plan_slot_id,
            "updatedAt": whole.doc.updated_at,
        }
    assert items[kit.id]["item"]["approvalCurrent"] is True
    assert items[draft.id]["item"]["reviewOnly"] is True
    assert "first try" not in response.text
