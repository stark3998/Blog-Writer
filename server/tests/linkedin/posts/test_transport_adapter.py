"""The staging transport adapter over ``blogwriter.linkedin.transport`` (imported on first use,
by the names its contract publishes)."""

from __future__ import annotations

import sys
from datetime import datetime
from types import ModuleType, SimpleNamespace

import pytest

from blogwriter.domain.ports import Services
from blogwriter.domain.testing import FakeServices
from blogwriter.linkedin.posts.staging import LinkedInTransport, StagingFailed, TokenCheck
from blogwriter.linkedin.posts.testing import T0

FAKE: str = "tests_fake_linkedin_transport"


class ReconnectRequired(Exception):
    def __init__(self, code: str = "needs_reconnect") -> None:
        super().__init__(code)
        self.code = code


class UploadFailed(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class FakeModule(ModuleType):
    def __init__(self) -> None:
        super().__init__(FAKE)
        self.ReconnectRequired = ReconnectRequired
        self.UploadFailed = UploadFailed
        self.upload_error: Exception | None = None
        self.access_error: Exception | None = None
        self.outcome = "active"
        self.uploaded: list[str] = []

    async def upload_post_media(self, services: Services, post_id: str) -> None:
        self.uploaded.append(post_id)
        if self.upload_error is not None:
            raise self.upload_error

    async def access_token(self, services: Services) -> tuple[str, str]:
        if self.access_error is not None:
            raise self.access_error
        return "token", "urn:li:person:abc"

    def current_client(self) -> object:
        return object()

    async def introspect_connection(
        self, services: Services, client: object, *, now: datetime
    ) -> SimpleNamespace:
        return SimpleNamespace(outcome=self.outcome)


@pytest.fixture
def module(monkeypatch: pytest.MonkeyPatch) -> FakeModule:
    fake = FakeModule()
    monkeypatch.setitem(sys.modules, FAKE, fake)
    return fake


async def test_uploads_and_maps_failures(services: FakeServices, module: FakeModule) -> None:
    transport = LinkedInTransport(FAKE)
    await transport.upload_media(services, "post-1")
    assert module.uploaded == ["post-1"]
    module.upload_error = ReconnectRequired("token_expired")
    with pytest.raises(StagingFailed) as reconnect:
        await transport.upload_media(services, "post-1")
    assert reconnect.value.code == "reconnect_required"
    module.upload_error = UploadFailed("media_changed")
    with pytest.raises(StagingFailed) as permanent:
        await transport.upload_media(services, "post-1")
    assert permanent.value.code == "media_changed"
    module.upload_error = UploadFailed("http_503")
    with pytest.raises(UploadFailed):  # transient: left to the runner's retry
        await transport.upload_media(services, "post-1")


async def test_checks_the_token(services: FakeServices, module: FakeModule) -> None:
    transport = LinkedInTransport(FAKE)
    assert await transport.check_token(services, now=T0) == TokenCheck(True)
    module.outcome = "inactive"
    assert await transport.check_token(services, now=T0) == TokenCheck(False, "reconnect_required")
    module.outcome = "failed"  # LinkedIn unreachable: not a verdict
    assert await transport.check_token(services, now=T0) == TokenCheck(True)
    module.access_error = ReconnectRequired("not_connected")
    assert await transport.check_token(services, now=T0) == TokenCheck(False, "reconnect_required")


async def test_a_missing_transport_is_not_configured(services: FakeServices) -> None:
    transport = LinkedInTransport("tests_no_such_module_anywhere")
    with pytest.raises(StagingFailed) as missing:
        await transport.upload_media(services, "post-1")
    assert missing.value.code == "linkedin_unavailable"


async def test_the_real_transport_without_a_connection(services: FakeServices) -> None:
    pytest.importorskip("blogwriter.linkedin.transport")
    check = await LinkedInTransport().check_token(services, now=T0)
    assert check == TokenCheck(False, "reconnect_required")
