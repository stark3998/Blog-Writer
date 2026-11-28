"""The transport lifecycle (settings, the shared client, the startup sweep) and the settings."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from pydantic import ValidationError

from blogwriter.db import Doc, Filter, MemoryStore, OrderBy, StoreUnavailable
from blogwriter.domain.base import to_iso
from blogwriter.domain.docstore import Stored, create_doc, read_doc
from blogwriter.domain.posts import Post, PublishIntent
from blogwriter.domain.states import PostStatus
from blogwriter.domain.testing import FakeServices
from blogwriter.jobs.testing import DelegatingStore
from blogwriter.linkedin.settings import LinkedInSettings, redirect_uri_for
from blogwriter.linkedin.transport.client import current_client
from blogwriter.linkedin.transport.lifecycle import TransportLifecycle, sweep_stale_intents
from blogwriter.linkedin.transport.testing import (
    CLIENT_SECRET,
    FakeLinkedIn,
    approved_post,
    fake_client,
    make_linkedin_settings,
)
from blogwriter.linkedin.transport.updates import revalidated

T0 = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)


async def publishing(
    services: FakeServices, age: timedelta, *, article_id: str | None = None
) -> Stored[Post]:
    post = approved_post(
        display="Hello.", lane="native", pillar="agents", format="text", article_id=article_id
    )
    assert post.approval is not None
    intent = PublishIntent(
        id=f"intent-{int(age.total_seconds()):08d}",
        created_at=to_iso(T0 - age),
        approval_sha256=post.approval.sha256,
    )
    return await create_doc(
        services.store, revalidated(post, status=PostStatus.PUBLISHING, intent=intent)
    )


def app_for(services: FakeServices) -> FastAPI:
    app = FastAPI()
    app.state.store = services.store
    app.state.clock = services.clock
    app.state.notifier = services.notifier
    return app


async def test_startup_sweeps_stale_intents_and_installs_the_client(
    services: FakeServices, caplog: pytest.LogCaptureFixture
) -> None:
    stale = await publishing(
        services, timedelta(minutes=5), article_id="art-0000000000000000000000000007"
    )
    fresh = await publishing(services, timedelta(seconds=30))
    settings = make_linkedin_settings()
    lifecycle = TransportLifecycle(settings)
    app = app_for(services)
    await lifecycle.startup(app)
    try:
        assert app.state.linkedin_settings is settings
        client = current_client()
        assert client is not None
        assert client.settings is settings
        swept = (await read_doc(services.store, Post, stale.doc.id)).doc
        assert swept.status is PostStatus.NEEDS_ATTENTION
        assert swept.intent is not None
        assert (swept.intent.status, swept.intent.error_code) == ("unknown", "publish_interrupted")
        assert swept.last_error == "publish_interrupted"
        untouched = await read_doc(services.store, Post, fresh.doc.id)
        assert untouched.etag == fresh.etag  # a live attempt elsewhere is left alone
        refs = [
            getattr(r, "ref_id", None) for r in caplog.records if r.getMessage() == "alert_event"
        ]
        assert refs == [stale.doc.id]
        (push, _urgency) = services.notifier.sent[0]
        assert push.url == f"/studio/art-0000000000000000000000000007/go-live?post={stale.doc.id}"
    finally:
        await lifecycle.shutdown(app)
    assert current_client() is None


async def test_a_provided_client_is_installed_but_not_closed(
    services: FakeServices, linkedin: FakeLinkedIn
) -> None:
    client = fake_client(linkedin)
    lifecycle = TransportLifecycle(make_linkedin_settings(), client=client)
    app = app_for(services)
    await lifecycle.startup(app)
    assert current_client() is client
    await lifecycle.shutdown(app)
    assert current_client() is None
    assert not vars(client)["_http"].is_closed
    await client.aclose()


class BrokenStore(DelegatingStore):
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
        raise StoreUnavailable("down")


async def test_a_failed_sweep_does_not_stop_the_boot(
    services: FakeServices, caplog: pytest.LogCaptureFixture
) -> None:
    app = FastAPI()
    app.state.store = BrokenStore(MemoryStore())
    lifecycle = TransportLifecycle(make_linkedin_settings())
    await lifecycle.startup(app)
    await lifecycle.shutdown(app)
    assert any(r.getMessage() == "linkedin_sweep_failed" for r in caplog.records)
    bare = FastAPI()
    await lifecycle.startup(bare)  # no store at all: the sweep is skipped
    await lifecycle.shutdown(bare)


async def test_the_sweep_settles_each_stale_post_once(services: FakeServices) -> None:
    stale = await publishing(services, timedelta(minutes=3))
    assert await sweep_stale_intents(services.store, now=T0) == [stale.doc.id]
    assert await sweep_stale_intents(services.store, now=T0) == []


# --------------------------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------------------------


def test_settings_defaults_are_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("CLIENT_ID", "CLIENT_SECRET", "REDIRECT_URI"):
        monkeypatch.delenv(f"BW_LINKEDIN_{name}", raising=False)
    settings = LinkedInSettings()
    assert not settings.configured
    assert (
        settings.timeout_seconds,
        settings.confirm_token_ttl_seconds,
        settings.upload_poll_seconds,
    ) == (
        20.0,
        600,
        120,
    )


def test_settings_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BW_LINKEDIN_CLIENT_ID", "abc123")
    monkeypatch.setenv("BW_LINKEDIN_CLIENT_SECRET", CLIENT_SECRET)
    monkeypatch.setenv("BW_LINKEDIN_TIMEOUT_SECONDS", "15")
    settings = LinkedInSettings()
    assert settings.configured
    assert settings.timeout_seconds == 15.0
    assert CLIENT_SECRET not in repr(settings)
    assert (
        settings.resolved_redirect_uri("https://writer.test")
        == "https://writer.test/api/linkedin/oauth/callback"
    )
    assert settings.resolved_redirect_uri(None) is None


@pytest.mark.parametrize(
    "uri",
    [
        "https://writer.test/api/linkedin/callback",
        "https://writer.test/api/linkedin/oauth/callback?next=/",
        "http://writer.test/api/linkedin/oauth/callback",
        "https://user:pw@writer.test/api/linkedin/oauth/callback",
    ],
)
def test_a_bad_redirect_uri_stops_the_boot(monkeypatch: pytest.MonkeyPatch, uri: str) -> None:
    monkeypatch.setenv("BW_LINKEDIN_REDIRECT_URI", uri)
    monkeypatch.setenv("BW_LINKEDIN_CLIENT_SECRET", CLIENT_SECRET)
    with pytest.raises(ValidationError) as caught:
        LinkedInSettings()
    assert CLIENT_SECRET not in str(caught.value)


def test_redirect_uris() -> None:
    local = make_linkedin_settings(redirect_uri="http://localhost:8000/api/linkedin/oauth/callback")
    assert local.redirect_uri == "http://localhost:8000/api/linkedin/oauth/callback"
    assert (
        redirect_uri_for("https://writer.test/")
        == "https://writer.test/api/linkedin/oauth/callback"
    )
    assert redirect_uri_for(None) is None
