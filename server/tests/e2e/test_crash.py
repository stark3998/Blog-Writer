"""Crash-only: the process dies in the middle of a step with a remote side effect (the remote
system already acted, its answer is still on the way), a new process (a new runner, a new
claim owner) takes the job over once the lease lapses, and the side effect happens once.

- the portfolio deploy dispatch of ``publish.article`` (GitHub took the dispatch);
- the preview dispatch of ``publish.staging``;
- a media upload of ``posts.stage`` at T-60 (LinkedIn has the bytes).
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

from blogwriter.domain.articles import Article
from blogwriter.domain.base import to_iso
from blogwriter.domain.docstore import read_doc
from blogwriter.domain.posts import Post
from blogwriter.domain.states import ArticleStatus, PostStatus
from blogwriter.linkedin.transport.client import API_BASE
from blogwriter.linkedin.transport.testing import IMAGE_URNS, POSTS_URL, UPLOAD_URL

from .conftest import (
    NEXT_MONDAY,
    REVIEWED_AT,
    STAGED_AT,
    System,
    approve_for_slot,
    connect_linkedin,
    derive_posts,
    draft_flagship,
    prepare_owner,
    review_and_publish,
    script_gates_again,
    start_system,
)

TAKEOVER = timedelta(seconds=66)
"""A dead owner's lease (60 s) plus the skew (5 s): another runner may take the job over."""


async def crash_and_restart(system: System, reached: asyncio.Event) -> None:
    """Wait until the remote call is in flight, kill the process, let the lease lapse and
    start a new one."""
    await asyncio.wait_for(reached.wait(), timeout=10)
    await system.crash()
    system.clock.advance(TAKEOVER)
    await system.boot()


async def ready_for_approval(system: System) -> str:
    """A flagship in review with its clearance and a passing gate report (Wednesday)."""
    owner = await prepare_owner(system)
    flagship = await draft_flagship(system, owner)
    path = f"/api/articles/{flagship.article_id}"
    await system.run_until(REVIEWED_AT)
    _, etag = await system.item(path)
    await system.ok("POST", f"{path}/clearance", {}, etag=etag)
    script_gates_again(system)
    rerun = await system.ok("POST", f"{path}/gates/run", status=202)
    await system.wait_job(str(rerun["jobId"]))
    await system.idle()
    return flagship.article_id


async def test_a_crash_mid_dispatch_never_dispatches_the_deploy_twice() -> None:
    async with start_system() as system:
        article_id = await ready_for_approval(system)
        _, etag = await system.item(f"/api/articles/{article_id}")
        approved = await system.ok("POST", f"/api/articles/{article_id}/approve", {}, etag=etag)
        job_id = str(approved["jobId"])
        await system.run_until(NEXT_MONDAY - timedelta(seconds=1))

        hold = asyncio.Event()
        system.portfolio.hold_dispatch = hold
        system.clock.set(NEXT_MONDAY)
        system.jobs.runner.wake()
        await crash_and_restart(system, system.portfolio.dispatch_reached)
        boots = system.boots
        hold.set()
        system.portfolio.hold_dispatch = None

        job = await system.wait_job(job_id)
        await system.idle()
        assert boots == 2
        assert system.portfolio.deploy_dispatches() == 1  # GitHub got exactly one dispatch
        dispatch = job.step("dispatch")
        assert dispatch is not None
        assert dispatch.runs == 2  # cut off once, resumed once
        assert dispatch.remote_ids["run"] == str(system.portfolio.dispatches[0].id)
        assert job.attempt == 2
        live = (await read_doc(system.store, Article, article_id)).doc
        assert live.status is ArticleStatus.PUBLISHED


async def test_a_crash_mid_dispatch_never_dispatches_the_preview_twice() -> None:
    async with start_system() as system:
        article_id = await ready_for_approval(system)
        hold = asyncio.Event()
        system.portfolio.hold_dispatch = hold
        staging = await system.ok("POST", f"/api/articles/{article_id}/staging-preview", status=202)
        await crash_and_restart(system, system.portfolio.dispatch_reached)
        hold.set()
        system.portfolio.hold_dispatch = None

        job = await system.wait_job(str(staging["jobId"]))
        await system.idle()
        assert [run.workflow for run in system.portfolio.dispatches] == ["preview.yml"]
        dispatch = job.step("dispatch")
        assert dispatch is not None
        assert dispatch.runs == 2
        assert dispatch.remote_ids["correlation"] == system.portfolio.dispatches[0].correlation


async def test_a_crash_mid_upload_never_uploads_twice() -> None:
    async with start_system() as system:
        owner = await prepare_owner(system)
        await connect_linkedin(system)
        flagship = await draft_flagship(system, owner)
        published = await review_and_publish(system, flagship)
        card_id, _ = await derive_posts(system, published)
        await approve_for_slot(system, card_id)
        await system.run_until(STAGED_AT - timedelta(seconds=1))

        hold = asyncio.Event()
        system.linkedin.hold_put = hold
        system.clock.set(STAGED_AT)
        system.jobs.runner.wake()
        await asyncio.wait_for(system.linkedin.put_reached.wait(), timeout=10)
        mid = (await read_doc(system.store, Post, card_id)).doc
        # The URN was recorded before the PUT: a crash cannot lose the remote id.
        assert [(m.linkedin_urn, m.linkedin_status) for m in mid.media] == [
            (IMAGE_URNS[0], "uploading")
        ]
        await crash_and_restart(system, system.linkedin.put_reached)
        hold.set()
        system.linkedin.hold_put = None
        await system.idle()

        staged = (await read_doc(system.store, Post, card_id)).doc
        assert staged.status is PostStatus.STAGED
        assert [(m.linkedin_urn, m.linkedin_status) for m in staged.media] == [
            (IMAGE_URNS[0], "available")
        ]
        assert staged.staging is not None
        assert staged.staging.staged_at == to_iso(STAGED_AT + TAKEOVER)
        linkedin = system.linkedin
        assert len(linkedin.calls("POST", f"{API_BASE}/images?action=initializeUpload")) == 1
        assert len(linkedin.calls("PUT", UPLOAD_URL)) == 1
        assert linkedin.calls("POST", POSTS_URL) == []  # staging never publishes
        assert system.pushes().count("Post at 10:00: tap to publish") == 1
