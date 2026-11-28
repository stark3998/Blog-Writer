"""The daily ``linkedin.introspect`` job on a real runner: introspection, token-day alerts and
the stale-intent sweep."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import pytest

from blogwriter.domain.base import to_iso
from blogwriter.domain.docstore import create_doc, read_doc
from blogwriter.domain.jobtypes import (
    ENTITY_CHANGED,
    LINKEDIN_INTROSPECT,
    PLANNER_POST_LIVE,
    PLANNER_POST_SCHEDULED,
    post_live_job_id,
    post_scheduled_job_id,
)
from blogwriter.domain.ports import services_installed
from blogwriter.domain.posts import KitInfo, Post, PublishIntent
from blogwriter.domain.states import ConnectionStatus, KitState, PostStatus
from blogwriter.domain.testing import FakeServices
from blogwriter.jobs import Job, JobRegistry, JobRunner
from blogwriter.jobs.testing import eventually, fast_settings
from blogwriter.linkedin.transport import register_jobs
from blogwriter.linkedin.transport.client import LinkedInClient, client_installed
from blogwriter.linkedin.transport.testing import (
    FakeLinkedIn,
    approved_post,
    connect_member,
    fake_client,
    make_linkedin_settings,
)
from blogwriter.linkedin.transport.tokens import read_connection
from blogwriter.linkedin.transport.updates import revalidated

T0 = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)
RUN_AT = T0 + timedelta(days=53, hours=8, minutes=30)


async def run_job(services: FakeServices, job_id: str = "sched-linkedin-introspect-1") -> Job:
    registry = JobRegistry()
    register_jobs(registry)
    job = await services.jobs.enqueue(LINKEDIN_INTROSPECT, None, job_id=job_id)
    runner = JobRunner(services.store, registry, settings=fast_settings(), clock=services.clock)
    with services_installed(services):
        await runner.start()
        try:

            async def done() -> bool:
                return (await services.jobs.get(job.id)).is_terminal

            await eventually(done, within=5)
        finally:
            await runner.stop()
    return await services.jobs.get(job.id)


def test_register_jobs_owns_only_the_introspect_job() -> None:
    registry = JobRegistry()
    register_jobs(registry)
    assert registry.names() == (LINKEDIN_INTROSPECT,)
    job_type = registry.get(LINKEDIN_INTROSPECT)
    assert job_type is not None
    assert job_type.step_names == ("introspect", "alerts", "sweep")


async def test_the_daily_job_checks_the_token_and_sends_due_alerts(
    services: FakeServices, linkedin: FakeLinkedIn, li_client: LinkedInClient
) -> None:
    del li_client
    assert services.vault is not None
    await connect_member(services.store, services.vault, at=T0)
    services.clock.set(RUN_AT)
    linkedin.introspection(active=True, expires_at=T0 + timedelta(days=60))
    job = await run_job(services)
    assert job.status == "succeeded", job.error
    conn = await read_connection(services.store, RUN_AT)
    assert conn.status is ConnectionStatus.CONNECTED
    assert conn.last_introspected_at == to_iso(RUN_AT)
    assert conn.introspection_active is True
    assert set(conn.alerts_sent) == {"53"}
    assert [p.title for p, _u in services.notifier.sent] == [
        "LinkedIn sign-in ends in 7 days: reconnect"
    ]
    changed = [e.data for e in job.events if e.type == ENTITY_CHANGED]
    assert {"entity": "linkedin_connection", "id": "linkedin-connection"} in changed
    assert job.result == {"swept": 0, "followups": 0}
    linkedin.introspection(active=True)
    again = await run_job(services, "sched-linkedin-introspect-2")  # the same day again
    assert again.status == "succeeded", again.error
    assert len(services.notifier.sent) == 1  # day 53 was alerted already


async def test_a_revoked_token_alerts_once(
    services: FakeServices,
    linkedin: FakeLinkedIn,
    li_client: LinkedInClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    del li_client
    caplog.set_level(logging.INFO)
    assert services.vault is not None
    await connect_member(services.store, services.vault, at=T0)
    services.clock.set(T0 + timedelta(days=10))
    linkedin.introspection(active=False)
    first = await run_job(services, "introspect-a")
    assert first.status == "succeeded", first.error
    conn = await read_connection(services.store, services.clock.now())
    assert (conn.status, conn.last_error) == (ConnectionStatus.NEEDS_RECONNECT, "token_inactive")
    titles = [p.title for p, _u in services.notifier.sent]
    assert titles == ["LinkedIn needs reconnecting: one-click publishing is off"]
    linkedin.introspection(active=False)
    second = await run_job(services, "introspect-b")
    assert second.status == "succeeded", second.error
    assert len(services.notifier.sent) == 1  # already needs_reconnect: no second alert
    refs = [getattr(r, "ref_id", None) for r in caplog.records if r.getMessage() == "alert_event"]
    assert refs == ["linkedin-connection"]


async def test_alerts_run_even_without_linkedin_credentials(
    services: FakeServices, linkedin: FakeLinkedIn
) -> None:
    assert services.vault is not None
    await connect_member(services.store, services.vault, at=T0)
    services.clock.set(T0 + timedelta(days=57, hours=9))
    client = fake_client(linkedin, make_linkedin_settings(client_id=None, client_secret=None))
    try:
        with client_installed(client):
            job = await run_job(services)
    finally:
        await client.aclose()
    assert job.status == "succeeded", job.error
    assert linkedin.requests == []
    conn = await read_connection(services.store, services.clock.now())
    assert set(conn.alerts_sent) == {"53", "57"}
    assert len(services.notifier.sent) == 1


async def test_the_job_sweeps_stale_intents(services: FakeServices) -> None:
    post = approved_post(display="Hello.", lane="native", pillar="agents", format="text")
    assert post.approval is not None
    intent = PublishIntent(
        id="intent-stale-0001",
        created_at=to_iso(T0 - timedelta(minutes=10)),
        approval_sha256=post.approval.sha256,
    )
    stored = await create_doc(
        services.store, revalidated(post, status=PostStatus.PUBLISHING, intent=intent)
    )
    job = await run_job(services)  # not connected, no client: introspection is a no-op
    assert job.status == "succeeded", job.error
    assert job.result == {"swept": 1, "followups": 0}
    after = await read_doc(services.store, Post, stored.doc.id)
    assert after.doc.status is PostStatus.NEEDS_ATTENTION
    assert after.doc.intent is not None
    assert after.doc.intent.status == "unknown"
    assert {"entity": "post", "id": stored.doc.id} in [
        e.data for e in job.events if e.type == ENTITY_CHANGED
    ]


async def test_lost_go_live_follow_ups_are_enqueued_again(services: FakeServices) -> None:
    """The post-live hook is best effort: a crash right after LinkedIn's 201 loses it. The daily
    sweep derives it again from the post (and the native-schedule hook from the kit)."""
    live_at = to_iso(T0 - timedelta(hours=2))
    went_live = approved_post(display="Live.", lane="native", pillar="agents", format="text")
    live = await create_doc(
        services.store,
        revalidated(went_live, status=PostStatus.POSTED, posted_at=live_at),
    )
    old = approved_post(display="Old.", lane="native", pillar="agents", format="text")
    await create_doc(
        services.store,
        revalidated(old, status=PostStatus.POSTED, posted_at=to_iso(T0 - timedelta(days=30))),
    )
    scheduled_at = to_iso(T0 + timedelta(days=1))
    kit_post = approved_post(display="Kit.", lane="native", pillar="agents", format="text")
    kit = await create_doc(
        services.store,
        revalidated(
            kit_post,
            status=PostStatus.KIT,
            go_live_mode="kit",
            kit=KitInfo(
                state=KitState.NATIVELY_SCHEDULED, prepared_at=live_at, scheduled_at=scheduled_at
            ),
        ),
    )
    job = await run_job(services)
    assert job.status == "succeeded", job.error
    assert job.result == {"swept": 0, "followups": 2}
    queued = await services.jobs.get(post_live_job_id(live.doc.id))
    assert queued.type == PLANNER_POST_LIVE
    assert queued.input == {"origin": "owner", "postId": live.doc.id, "goLiveAt": live_at}
    scheduled = await services.jobs.get(post_scheduled_job_id(kit.doc.id, scheduled_at))
    assert scheduled.type == PLANNER_POST_SCHEDULED
    again = await run_job(services, "introspect-again")  # reused, never twice
    assert again.status == "succeeded", again.error
    assert (await services.jobs.get(post_live_job_id(live.doc.id))).id == queued.id
