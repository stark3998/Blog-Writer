"""Live check (``BW_LIVE_COSMOS=1``): two runners on the real v2 Cosmos account.

Safety, enforced by :class:`LiveGuard` rather than by test discipline:

- writes may only touch ids starting with ``test-`` in the ``jobs`` container; anything else
  fails the test before a request is sent;
- every write is recorded before it is sent, so a write whose response was lost is still
  cleaned up;
- the job type is unique to the run (``test-two-runner-<run>``), so no other runner, a deployed
  app included, would ever claim these jobs;
- documents carry ``ttl: 3600``; teardown (which also runs on failure) deletes every recorded id
  and sweeps the container for the run's job type.

The guard itself is tested offline (always runs).
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections import Counter
from collections.abc import Mapping
from typing import Any, override

import pytest
from azure.identity.aio import AzureCliCredential
from pydantic import JsonValue

from blogwriter.db import CosmosStore, Doc, Filter, MemoryStore, NotFound, Store, StoreError
from blogwriter.jobs import JobRegistry, JobRunner, Step, StepContext, enqueue
from blogwriter.jobs.models import Job
from blogwriter.jobs.testing import DelegatingStore, eventually, fast_settings

LIVE = os.environ.get("BW_LIVE_COSMOS") == "1"


class LiveGuard(DelegatingStore):
    """Admits writes to ``test-`` ids in ``jobs`` only, and records them before sending."""

    def __init__(self, inner: Store, written: list[str]) -> None:
        super().__init__(inner)
        self.written = written

    def _admit(self, container: str, item_id: object) -> None:
        if container != "jobs":
            raise AssertionError(f"live jobs tests may not write to {container!r}")
        if not isinstance(item_id, str) or not item_id.startswith("test-"):
            raise AssertionError("live jobs tests may only write ids starting with 'test-'")
        self.written.append(item_id)

    @override
    async def create(self, container: str, body: Mapping[str, Any]) -> Doc:
        self._admit(container, body.get("id"))
        return await self.inner.create(container, body)

    @override
    async def replace(
        self, container: str, body: Mapping[str, Any], *, if_match: str | None = None
    ) -> Doc:
        self._admit(container, body.get("id"))
        return await self.inner.replace(container, body, if_match=if_match)

    @override
    async def upsert(self, container: str, body: Mapping[str, Any]) -> Doc:
        self._admit(container, body.get("id"))
        return await self.inner.upsert(container, body)

    @override
    async def delete(
        self,
        container: str,
        id: str,
        *,
        partition_key: str | None = None,
        if_match: str | None = None,
    ) -> None:
        self._admit(container, id)
        await self.inner.delete(container, id, partition_key=partition_key, if_match=if_match)


async def _cleanup(store: Store, written: list[str], job_type: str) -> list[str]:
    """Delete everything the run wrote; returns what could not be deleted."""
    failures: list[str] = []
    for item_id in dict.fromkeys(reversed(written)):
        try:
            await store.delete("jobs", item_id)
        except NotFound:
            pass
        except StoreError as exc:
            failures.append(f"{item_id}: {type(exc).__name__}")
    try:
        leftovers = await store.find("jobs", [Filter("type", "==", job_type)])
    except StoreError as exc:
        return [*failures, f"sweep: {type(exc).__name__}"]
    for doc in leftovers:
        if doc.id.startswith("test-"):
            try:
                await store.delete("jobs", doc.id)
            except StoreError as exc:
                failures.append(f"{doc.id}: {type(exc).__name__}")
    return failures


def counting_registry(job_type: str, calls: Counter[tuple[str, str]]) -> JobRegistry:
    def counted(name: str) -> Step:
        async def run(ctx: StepContext) -> JsonValue:
            calls[(ctx.job_id, ctx.step)] += 1
            if name == "b":
                await ctx.record_remote_id("op", f"op-{ctx.job_id}")
                await ctx.emit("progress", {"step": name})
            return {"step": name}

        return Step(name, run)

    registry = JobRegistry()
    registry.register(job_type, [counted("a"), counted("b"), counted("c")])
    return registry


@pytest.mark.skipif(not LIVE, reason="needs BW_LIVE_COSMOS=1, BW_COSMOS_ENDPOINT and az login")
async def test_two_runners_on_cosmos_run_every_step_exactly_once() -> None:
    run = uuid.uuid4().hex[:10]
    job_type = f"test-two-runner-{run}"
    written: list[str] = []
    credential = AzureCliCredential()
    inner = await CosmosStore.from_settings(credential=credential)
    store = LiveGuard(inner, written)
    calls: Counter[tuple[str, str]] = Counter()
    registry = counting_registry(job_type, calls)
    settings = fast_settings(concurrency=2, poll_seconds=0.2, store_timeout_seconds=30)
    first = JobRunner(store, registry, settings=settings, owner=f"test-a-{run}:1")
    second = JobRunner(store, registry, settings=settings, owner=f"test-b-{run}:2")
    try:
        jobs = [
            await enqueue(store, job_type, {"n": n}, job_id=f"test-{run}-{n}", ttl=3600)
            for n in range(6)
        ]
        await first.start()
        await second.start()

        async def finished() -> bool:
            docs = await inner.find("jobs", [Filter("type", "==", job_type)])
            return len(docs) == 6 and all(doc.body["status"] == "succeeded" for doc in docs)

        await eventually(finished, within=180, interval=0.5)
        await first.stop()
        await second.stop()

        stored = [Job.from_body((await inner.read("jobs", job.id)).body) for job in jobs]
        assert calls == Counter({(job.id, step): 1 for job in jobs for step in "abc"})
        for job in stored:
            assert (job.status, job.fence, job.attempt, job.owner) == ("succeeded", 1, 1, None)
            assert [step.runs for step in job.steps] == [1, 1, 1]
            assert job.steps[1].remote_ids == {"op": f"op-{job.id}"}
            assert job.ttl == 3600
        assert first.stats.claimed + second.stats.claimed == 6
        assert first.stats.claimed > 0
        assert second.stats.claimed > 0
        assert first.stats.claims_lost == second.stats.claims_lost == 0
    finally:
        await first.stop(release=False)
        await second.stop(release=False)
        await asyncio.sleep(0)
        try:
            failures = await _cleanup(store, written, job_type)
        finally:
            await inner.close()
            await credential.close()
        assert failures == [], f"live cleanup incomplete (ttl expires them): {failures}"


async def test_the_live_guard_blocks_everything_but_test_ids_in_jobs() -> None:
    written: list[str] = []
    store = LiveGuard(MemoryStore(), written)
    with pytest.raises(AssertionError, match="may not write"):
        await store.upsert("settings", {"id": "test-x"})
    with pytest.raises(AssertionError, match="test-"):
        await store.create("jobs", {"id": "real-job"})
    with pytest.raises(AssertionError, match="test-"):
        await store.delete("jobs", "job-1")
    assert written == []
    await store.create("jobs", {"id": "test-a"})
    await store.replace("jobs", {"id": "test-a", "n": 1})
    await store.delete("jobs", "test-a")
    assert written == ["test-a", "test-a", "test-a"]
    assert await _cleanup(store, written, "test-none") == []
