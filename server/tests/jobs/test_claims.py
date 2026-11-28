"""Claims: acquire/renew/release with If-Match, fencing, re-read on conflict, unknown outcomes."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, override

import pytest

from blogwriter.db import Doc, MemoryStore, Store, StoreUnavailable
from blogwriter.jobs.claims import (
    Body,
    Claim,
    ClaimLost,
    LeasePolicy,
    acquire,
    cas_update,
    new_owner,
)
from blogwriter.jobs.clock import to_iso
from blogwriter.jobs.testing import DelegatingStore, FakeClock

T0 = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)
POLICY = LeasePolicy(
    lease=timedelta(seconds=60),
    renew_every=timedelta(seconds=15),
    skew=timedelta(seconds=5),
    store_timeout=5.0,
)
ITEM = "test-claim"
WRITE_TOKEN = "write-1"  # a writeId token (not a credential)  # noqa: S105


class FaultyStore(DelegatingStore):
    """The next replace can fail before it is applied (lost request) or after (lost response)."""

    def __init__(self, inner: Store) -> None:
        super().__init__(inner)
        self.fail_next_replace: Literal["before", "after"] | None = None
        self.failing_reads = 0
        self.replaces = 0

    @override
    async def replace(
        self, container: str, body: Mapping[str, Any], *, if_match: str | None = None
    ) -> Doc:
        self.replaces += 1
        mode, self.fail_next_replace = self.fail_next_replace, None
        if mode == "before":
            raise StoreUnavailable("request lost")
        doc = await self.inner.replace(container, body, if_match=if_match)
        if mode == "after":
            raise StoreUnavailable("response lost")
        return doc

    @override
    async def read(self, container: str, id: str, *, partition_key: str | None = None) -> Doc:
        if self.failing_reads:
            self.failing_reads -= 1
            raise StoreUnavailable("read failed")
        return await self.inner.read(container, id, partition_key=partition_key)


async def put(store: Store, **fields: object) -> Doc:
    return await store.create("jobs", {"id": ITEM, "kind": "thing", "owner": None, **fields})


async def claim_it(store: Store, clock: FakeClock, owner: str = "a:1") -> Claim:
    doc = await store.read("jobs", ITEM)
    claim = await acquire(store, "jobs", doc, owner=owner, policy=POLICY, clock=clock)
    assert claim is not None
    return claim


def append(entry: str) -> Any:
    def mutate(body: Body) -> Body:
        log: list[str] = list(body.get("log", []))
        return {**body, "log": [*log, entry]}

    return mutate


async def test_acquire_sets_owner_and_lease_and_increments_the_fence(
    store: MemoryStore, clock: FakeClock
) -> None:
    await put(store, fence=4)
    claim = await claim_it(store, clock)
    stored = (await store.read("jobs", ITEM)).body
    assert stored["owner"] == "a:1"
    assert stored["fence"] == 5
    assert stored["leaseUntil"] == to_iso(T0 + timedelta(seconds=60))
    assert isinstance(stored["writeId"], str)
    assert claim.fence == 5
    assert claim.lease_until == T0 + timedelta(seconds=60)
    assert claim.deadline == T0 + timedelta(seconds=55)
    assert claim.held
    assert claim.etag == (await store.read("jobs", ITEM)).etag


async def test_a_missing_fence_starts_at_one(store: MemoryStore, clock: FakeClock) -> None:
    await put(store)
    assert (await claim_it(store, clock)).fence == 1


async def test_only_one_of_two_racing_acquirers_wins(store: MemoryStore, clock: FakeClock) -> None:
    doc = await put(store)
    results = await asyncio.gather(
        acquire(store, "jobs", doc, owner="a:1", policy=POLICY, clock=clock),
        acquire(store, "jobs", doc, owner="b:2", policy=POLICY, clock=clock),
    )
    winners = [claim for claim in results if claim is not None]
    assert len(winners) == 1
    stored = (await store.read("jobs", ITEM)).body
    assert stored["owner"] == winners[0].owner
    assert stored["fence"] == 1


async def test_a_lease_is_taken_over_only_after_lease_plus_skew(
    store: MemoryStore, clock: FakeClock
) -> None:
    await put(store)
    await claim_it(store, clock)
    held = await store.read("jobs", ITEM)
    clock.advance(64.9)
    assert await acquire(store, "jobs", held, owner="b:2", policy=POLICY, clock=clock) is None
    clock.advance(0.2)
    takeover = await acquire(store, "jobs", held, owner="b:2", policy=POLICY, clock=clock)
    assert takeover is not None
    assert takeover.fence == 2


async def test_a_stale_holder_cannot_commit_after_a_takeover(
    store: MemoryStore, clock: FakeClock
) -> None:
    await put(store)
    stale = await claim_it(store, clock, "a:1")
    clock.advance(66)
    fresh = await claim_it(store, clock, "b:2")
    with pytest.raises(ClaimLost):
        await stale.commit(lambda body: {**body, "result": "stale"})
    stored = (await store.read("jobs", ITEM)).body
    assert stored["owner"] == "b:2"
    assert stored["fence"] == 2
    assert "result" not in stored
    assert stale.lost
    with pytest.raises(ClaimLost):
        await stale.renew()
    body = await fresh.commit(lambda body: {**body, "result": "fresh"})
    assert body["result"] == "fresh"


async def test_the_fence_stops_a_stale_holder_even_when_it_is_the_same_owner(
    store: MemoryStore, clock: FakeClock
) -> None:
    """A replica that re-acquires its own lapsed job gets a new fence; the old claim is dead."""
    await put(store)
    old = await claim_it(store, clock, "a:1")
    clock.advance(66)
    new = await claim_it(store, clock, "a:1")
    assert new.fence == old.fence + 1
    with pytest.raises(ClaimLost):
        await old.commit(lambda body: {**body, "result": "old"})
    assert "result" not in (await store.read("jobs", ITEM)).body


async def test_a_foreign_write_is_merged_and_the_claim_kept(
    store: MemoryStore, clock: FakeClock
) -> None:
    await put(store)
    claim = await claim_it(store, clock)
    current = await store.read("jobs", ITEM)  # the API sets a flag without touching the claim
    await store.replace("jobs", {**current.body, "cancelRequested": True}, if_match=current.etag)
    body = await claim.commit(lambda body: {**body, "progress": 1})
    assert body["cancelRequested"] is True
    assert body["progress"] == 1
    assert body["owner"] == "a:1"
    assert claim.held


async def test_a_write_whose_response_was_lost_is_applied_once(
    store: MemoryStore, clock: FakeClock
) -> None:
    faulty = FaultyStore(store)
    await put(store)
    claim = await claim_it(faulty, clock)
    faulty.fail_next_replace = "after"
    body = await claim.commit(append("x"))
    assert body["log"] == ["x"]
    assert (await store.read("jobs", ITEM)).body["log"] == ["x"]
    assert (await claim.commit(append("y")))["log"] == ["x", "y"]


async def test_a_write_whose_request_was_lost_is_retried(
    store: MemoryStore, clock: FakeClock
) -> None:
    faulty = FaultyStore(store)
    await put(store)
    claim = await claim_it(faulty, clock)
    faulty.fail_next_replace = "before"
    body = await claim.commit(append("x"))
    assert body["log"] == ["x"]
    assert faulty.replaces == 3  # acquire, lost attempt, retry


async def test_retrying_a_logical_write_with_its_token_never_applies_it_twice(
    store: MemoryStore, clock: FakeClock
) -> None:
    faulty = FaultyStore(store)
    await put(store)
    claim = await claim_it(faulty, clock)
    faulty.fail_next_replace = "after"
    faulty.failing_reads = 1  # and the follow-up read fails too: outcome unknown
    with pytest.raises(StoreUnavailable):
        await claim.commit(append("x"), token=WRITE_TOKEN)
    assert claim.held
    body = await claim.commit(append("x"), token=WRITE_TOKEN)
    assert body["log"] == ["x"]


async def test_renew_extends_the_lease(store: MemoryStore, clock: FakeClock) -> None:
    await put(store)
    claim = await claim_it(store, clock)
    clock.advance(15)
    await claim.renew()
    assert claim.lease_until == T0 + timedelta(seconds=75)
    stored = (await store.read("jobs", ITEM)).body
    assert stored["leaseUntil"] == to_iso(T0 + timedelta(seconds=75))
    assert stored["fence"] == 1


async def test_release_clears_the_claim_and_ends_it(store: MemoryStore, clock: FakeClock) -> None:
    await put(store)
    claim = await claim_it(store, clock)
    body = await claim.release(lambda body: {**body, "done": True})
    assert body["owner"] is None
    assert body["leaseUntil"] is None
    assert body["done"] is True
    assert body["fence"] == 1
    assert not claim.held
    with pytest.raises(ClaimLost):
        await claim.renew()
    assert POLICY.is_claimable(body, clock.now())


async def test_a_deleted_document_ends_the_claim(store: MemoryStore, clock: FakeClock) -> None:
    await put(store)
    claim = await claim_it(store, clock)
    await store.delete("jobs", ITEM)
    with pytest.raises(ClaimLost):
        await claim.commit(append("x"))
    assert claim.lost


async def test_mutations_may_not_change_the_id(store: MemoryStore, clock: FakeClock) -> None:
    doc = await put(store)
    with pytest.raises(ValueError, match="id"):
        await acquire(
            store,
            "jobs",
            doc,
            owner="a:1",
            policy=POLICY,
            clock=clock,
            mutate=lambda body: {**body, "id": "other"},
        )
    claim = await claim_it(store, clock)
    with pytest.raises(ValueError, match="id"):
        await claim.commit(lambda body: {**body, "id": "other"})


async def test_mutations_cannot_forge_the_claim_fields(
    store: MemoryStore, clock: FakeClock
) -> None:
    await put(store)
    claim = await claim_it(store, clock)
    body = await claim.commit(lambda body: {**body, "owner": "intruder", "fence": 99})
    assert body["owner"] == "a:1"
    assert body["fence"] == 1


async def test_acquire_with_an_unknown_outcome_checks_its_token(
    store: MemoryStore, clock: FakeClock
) -> None:
    faulty = FaultyStore(store)
    doc = await put(store)
    faulty.fail_next_replace = "before"
    assert await acquire(faulty, "jobs", doc, owner="a:1", policy=POLICY, clock=clock) is None
    faulty.fail_next_replace = "after"
    claim = await acquire(faulty, "jobs", doc, owner="a:1", policy=POLICY, clock=clock)
    assert claim is not None
    assert (await store.read("jobs", ITEM)).body["owner"] == "a:1"


async def test_acquire_refuses_held_and_corrupt_documents(
    store: MemoryStore, clock: FakeClock
) -> None:
    doc = await put(store, fence="seven")
    assert await acquire(store, "jobs", doc, owner="a:1", policy=POLICY, clock=clock) is None
    future = to_iso(T0 + timedelta(seconds=30))
    assert not POLICY.is_claimable({"owner": "x", "leaseUntil": future}, T0)
    assert POLICY.is_claimable({"owner": None}, T0)
    assert POLICY.is_claimable({"owner": "x"}, T0)  # no lease: can never be renewed
    assert POLICY.is_claimable({"owner": "x", "leaseUntil": "garbage"}, T0)
    assert POLICY.takeover_cutoff(T0) == to_iso(T0 - timedelta(seconds=5))


async def test_cas_update_rereads_after_a_conflict(store: MemoryStore) -> None:
    stale = await put(store, n=0)
    current = await store.replace("jobs", {**stale.body, "n": 1}, if_match=stale.etag)
    seen: list[int] = []

    def increment(body: Body) -> Body:
        seen.append(body["n"])
        return {**body, "n": body["n"] + 1}

    updated = await cas_update(store, "jobs", ITEM, increment, store_timeout=5, current=stale)
    assert updated is not None
    assert seen == [0, 1]
    assert updated.body["n"] == 2
    assert updated.etag != current.etag
    assert await cas_update(store, "jobs", ITEM, lambda body: None, store_timeout=5) is None


def test_owners_are_replica_plus_boot_uuid() -> None:
    first, second = new_owner("replica-1"), new_owner("replica-1")
    assert first.startswith("replica-1:")
    assert len(first.split(":")[1]) == 32
    assert first != second


@pytest.mark.parametrize("lost", ["after", "before"])
async def test_an_in_doubt_write_is_settled_before_any_later_write_of_the_claim(
    store: MemoryStore, clock: FakeClock, lost: Literal["before", "after"]
) -> None:
    """A renewal landing on top of an in-doubt write would hide its token; a retry with that
    token would then apply it again. The claim settles the in-doubt write first instead."""
    faulty = FaultyStore(store)
    await put(store)
    claim = await claim_it(faulty, clock)
    faulty.fail_next_replace = lost  # "after": it landed; "before": it did not
    faulty.failing_reads = 1  # and the follow-up read fails: outcome unknown
    with pytest.raises(StoreUnavailable):
        await claim.commit(append("x"), token=WRITE_TOKEN)
    renewed = await claim.renew()  # the lease keeper writes in between
    assert renewed["log"] == ["x"]
    assert renewed["writeId"] != WRITE_TOKEN
    retried = await claim.commit(append("x"), token=WRITE_TOKEN)  # the caller retries
    assert retried["log"] == ["x"]
    assert (await store.read("jobs", ITEM)).body["log"] == ["x"]
    assert (await claim.commit(append("y")))["log"] == ["x", "y"]


async def test_an_in_doubt_release_is_settled_before_a_renewal(
    store: MemoryStore, clock: FakeClock
) -> None:
    faulty = FaultyStore(store)
    await put(store)
    claim = await claim_it(faulty, clock)
    faulty.fail_next_replace = "before"
    faulty.failing_reads = 1
    with pytest.raises(StoreUnavailable):
        await claim.release(lambda body: {**body, "done": True}, token=WRITE_TOKEN)
    assert claim.held  # unknown outcome: still ours as far as we know
    with pytest.raises(ClaimLost, match="released"):
        await claim.renew()  # settles the release first, then there is nothing to renew
    assert not claim.held
    body = await claim.release(lambda body: {**body, "done": True}, token=WRITE_TOKEN)
    assert body["done"] is True
    stored = (await store.read("jobs", ITEM)).body
    assert (stored["owner"], stored["leaseUntil"], stored["done"]) == (None, None, True)


async def test_a_lost_claim_abandons_its_in_doubt_write(
    store: MemoryStore, clock: FakeClock
) -> None:
    faulty = FaultyStore(store)
    await put(store)
    claim = await claim_it(faulty, clock)
    faulty.fail_next_replace = "before"
    faulty.failing_reads = 1
    with pytest.raises(StoreUnavailable):
        await claim.commit(append("x"), token=WRITE_TOKEN)
    claim.mark_lost()  # self-fenced: no further writes, the in-doubt one included
    with pytest.raises(ClaimLost):
        await claim.commit(append("x"), token=WRITE_TOKEN)
    assert "log" not in (await store.read("jobs", ITEM)).body
