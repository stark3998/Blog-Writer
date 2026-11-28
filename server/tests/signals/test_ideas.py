"""The ideas inbox helpers: use, expiry, snooze wake-ups and ids."""

from __future__ import annotations

from datetime import timedelta

import pytest

from blogwriter.db import NotFound
from blogwriter.domain.base import sha256_hex, to_iso
from blogwriter.domain.docstore import create_doc, read_doc
from blogwriter.domain.signals import Idea
from blogwriter.domain.states import IdeaStatus, TransitionError
from blogwriter.domain.testing import FakeServices, sample_document
from blogwriter.signals import expire_ideas, idea_id_for_url, mark_idea_used
from blogwriter.signals.ideas import expires_for, wake_snoozed_ideas
from blogwriter.signals.testing import T0


async def add(services: FakeServices, **fields: object) -> Idea:
    idea = sample_document(Idea, **fields)
    await create_doc(services.store, idea)
    return idea


async def status_of(services: FakeServices, idea_id: str) -> Idea:
    return (await read_doc(services.store, Idea, idea_id)).doc


async def test_mark_idea_used_from_new_and_accepted(services: FakeServices) -> None:
    new = await add(services)
    accepted = await add(services, status="accepted")
    later = T0 + timedelta(minutes=5)
    stored = await mark_idea_used(services.store, new.id, now=later, article_id="art-1")
    assert stored.doc.status is IdeaStatus.USED
    assert stored.doc.article_id == "art-1"
    assert stored.doc.status_changed_at == to_iso(later)
    used = await mark_idea_used(services.store, accepted.id, now=later, post_id="post-1")
    assert used.doc.post_id == "post-1"
    assert used.doc.status is IdeaStatus.USED


async def test_mark_idea_used_is_idempotent_and_fills_missing_refs(services: FakeServices) -> None:
    idea = await add(services)
    first = await mark_idea_used(services.store, idea.id, now=T0, article_id="art-1")
    again = await mark_idea_used(services.store, idea.id, now=T0, article_id="art-2")
    assert again.etag == first.etag  # nothing written: the first article stays
    assert again.doc.article_id == "art-1"
    with_post = await mark_idea_used(services.store, idea.id, now=T0, post_id="post-9")
    assert with_post.doc.post_id == "post-9"
    assert with_post.doc.article_id == "art-1"


@pytest.mark.parametrize("status", ["dismissed", "expired", "snoozed"])
async def test_mark_idea_used_follows_the_machine(services: FakeServices, status: str) -> None:
    idea = await add(services, status=status)
    with pytest.raises(TransitionError):
        await mark_idea_used(services.store, idea.id, now=T0, article_id="art-1")
    with pytest.raises(NotFound):
        await mark_idea_used(services.store, "idea-missing", now=T0)


async def test_expire_ideas_only_touches_due_new_and_snoozed(services: FakeServices) -> None:
    past = to_iso(T0 - timedelta(minutes=1))
    future = to_iso(T0 + timedelta(hours=1))
    due_new = await add(services, time_sensitive=True, expires_at=past)
    due_snoozed = await add(services, status="snoozed", time_sensitive=True, expires_at=past)
    due_accepted = await add(services, status="accepted", time_sensitive=True, expires_at=past)
    not_due = await add(services, time_sensitive=True, expires_at=future)
    plain = await add(services)
    assert await expire_ideas(services.store, now=T0) == 2
    assert (await status_of(services, due_new.id)).status is IdeaStatus.EXPIRED
    snoozed = await status_of(services, due_snoozed.id)
    assert snoozed.status is IdeaStatus.EXPIRED
    assert snoozed.status_changed_at == to_iso(T0)
    for unchanged in (due_accepted, not_due, plain):
        assert (await status_of(services, unchanged.id)).status is unchanged.status
    assert await expire_ideas(services.store, now=T0) == 0


async def test_snoozed_ideas_wake_up(services: FakeServices) -> None:
    due = await add(services, status="snoozed", snoozed_until=to_iso(T0 - timedelta(seconds=1)))
    later = await add(services, status="snoozed", snoozed_until=to_iso(T0 + timedelta(days=1)))
    forever = await add(services, status="snoozed")
    assert await wake_snoozed_ideas(services.store, now=T0) == 1
    woken = await status_of(services, due.id)
    assert woken.status is IdeaStatus.NEW
    assert woken.snoozed_until is None
    assert (await status_of(services, later.id)).status is IdeaStatus.SNOOZED
    assert (await status_of(services, forever.id)).status is IdeaStatus.SNOOZED


def test_expiry_window() -> None:
    published = to_iso(T0 - timedelta(hours=10))
    assert expires_for(published_at=published, now=T0, max_age_hours=72) == to_iso(
        T0 + timedelta(hours=62)
    )
    assert expires_for(published_at=None, now=T0, max_age_hours=72) == to_iso(
        T0 + timedelta(hours=72)
    )
    old = to_iso(T0 - timedelta(hours=80))
    assert expires_for(published_at=old, now=T0, max_age_hours=72) is None
    ahead = to_iso(T0 + timedelta(hours=5))  # a feed clock ahead of ours counts from now
    assert expires_for(published_at=ahead, now=T0, max_age_hours=72) == to_iso(
        T0 + timedelta(hours=72)
    )


def test_idea_ids_come_from_the_url_hash() -> None:
    digest = sha256_hex("https://example.com/a")
    assert idea_id_for_url(digest) == f"idea-{digest[:24]}"
    with pytest.raises(ValueError, match="SHA-256"):
        idea_id_for_url("abc")
