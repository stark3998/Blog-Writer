"""Invariants of the publish package (plan "Data" > published-blogs v2, "Security model", contract
sections 1.7-1.9): only contract-valid documents are ever written, never future-dated; a
contract violation writes nothing; push notices carry a short title and a same-origin path;
the token, titles and article text never reach logs, and the token never reaches the store; the
content job types are never scheduled."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, override

import pytest

from blogwriter.articles import transition
from blogwriter.core.testing import asgi_client
from blogwriter.db import Doc, MemoryStore, Store, StoreUnavailable
from blogwriter.domain.articles import Article
from blogwriter.domain.docstore import read_doc
from blogwriter.domain.jobtypes import (
    PUBLISH_ARTICLE,
    PUBLISH_STAGING,
    PUBLISH_UNPUBLISH,
    SCHEDULED_JOB_TYPES,
    generates_content,
)
from blogwriter.domain.states import ArticleStatus
from blogwriter.domain.testing import FakeClock, make_services
from blogwriter.jobs import JobRegistry
from blogwriter.jobs.testing import DelegatingStore
from blogwriter.publish import published as published_module
from blogwriter.publish.contract import ContractError, validate_published_doc
from blogwriter.publish.document import contract_time
from blogwriter.publish.jobs import PUBLISH_STEPS, STAGING_STEPS, UNPUBLISH_STEPS, register_jobs
from blogwriter.publish.testing import (
    TEST_TOKEN,
    PublishHarness,
    make_harness,
    owner_headers,
    publish_app,
    seed_article,
)

S = ArticleStatus
MONDAY = datetime(2026, 11, 30, 14, 0, tzinfo=UTC)
CONTAINERS = ("published-blogs", "published-blogs-staging")


class ValidatingStore(DelegatingStore):
    """Checks every published-blogs write against the contract at the moment it happens."""

    def __init__(self, inner: Store, clock: FakeClock) -> None:
        super().__init__(inner)
        self.clock = clock
        self.checked: list[str] = []
        self.problems: list[str] = []

    def _check(self, container: str, body: Mapping[str, Any]) -> None:
        if container not in CONTAINERS:
            return
        now = self.clock.now()
        errors = validate_published_doc(body, now=now)
        self.problems.extend(f"{error.code}:{error.path}" for error in errors)
        published = body.get("publishedAt")
        if isinstance(published, str) and published > contract_time(now):
            self.problems.append("published_in_future")
        self.checked.append(f"{container}/{body.get('id')}:{body.get('status')}")

    @override
    async def create(self, container: str, body: Mapping[str, Any]) -> Doc:
        self._check(container, body)
        return await super().create(container, body)

    @override
    async def upsert(self, container: str, body: Mapping[str, Any]) -> Doc:
        self._check(container, body)
        return await super().upsert(container, body)

    @override
    async def replace(
        self, container: str, body: Mapping[str, Any], *, if_match: str | None = None
    ) -> Doc:
        self._check(container, body)
        return await super().replace(container, body, if_match=if_match)


async def post(harness: PublishHarness, article_id: str, action: str, body: object = None) -> Any:
    etag = (await read_doc(harness.services.store, Article, article_id)).etag
    async with asgi_client(publish_app(harness)) as client:
        response = await client.post(
            f"/api/articles/{article_id}/{action}", json=body, headers=owner_headers(etag)
        )
    assert response.status_code in (200, 202), response.text
    return response.json()


async def test_every_published_write_is_contract_valid_and_never_future_dated(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    clock = FakeClock()
    spy = ValidatingStore(MemoryStore(), clock)
    services = make_services(store=spy, clock=clock)
    harness = make_harness(services)
    article = await seed_article(services)
    approved = await post(harness, article.id, "approve", {})
    clock.set(MONDAY)
    assert (await harness.run_job(approved["jobId"])).status == "succeeded"

    clock.advance(timedelta(days=1))
    staged = await post(harness, article.id, "staging-preview")
    assert (await harness.run_job(staged["jobId"])).status == "succeeded"
    unpublished = await post(harness, article.id, "unpublish")
    assert (await harness.run_job(unpublished["jobId"])).status == "succeeded"
    await transition(services.store, article.id, S.IN_REVIEW, actor="owner", now=clock.now())

    assert spy.problems == []
    assert sorted(set(spy.checked)) == [
        "published-blogs-staging/scope-every-tool-call:published",
        "published-blogs/scope-every-tool-call:published",
        "published-blogs/scope-every-tool-call:unpublished",
    ]

    # Nothing secret or contentful in the logs, and the token nowhere in the store.
    secrets = (TEST_TOKEN, article.meta.title or "", "borrowed authority", "<h2")
    for record in caplog.records:
        line = record.getMessage() + json.dumps(record.__dict__, default=str)
        for secret in secrets:
            assert secret not in line, (record.getMessage(), secret)
    for container in ("articles", "article-versions", "jobs", "settings", *CONTAINERS):
        dump = json.dumps([doc.body for doc in await spy.find(container)])
        assert TEST_TOKEN not in dump
    assert f"Bearer {TEST_TOKEN}" in harness.portfolio.authorizations  # it was used

    # Push notices: a short title and a same-origin path, nothing else.
    for payload, _ in services.notifier.sent:
        assert 0 < len(payload.title) <= 80
        assert payload.url.startswith("/")
        assert not payload.url.startswith("//")
        assert set(json.loads(payload.to_json())) == {"title", "url", "tag"}


async def test_a_contract_violation_writes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(doc: Mapping[str, object], *, now: datetime | None = None) -> list[ContractError]:
        return [ContractError("html-outside-profile", "/htmlContent", "not allowed")]

    monkeypatch.setattr(published_module, "validate_published_doc", refuse)
    harness = make_harness()
    services = harness.services
    article = await seed_article(services)
    approved = await post(harness, article.id, "approve", {})
    services.clock.set(MONDAY)
    job = await harness.run_job(approved["jobId"])
    assert job.error is not None
    assert job.error.code == "contract_invalid"
    assert await services.store.find("published-blogs") == []
    assert harness.portfolio.runs == {}
    stored = (await read_doc(services.store, Article, article.id)).doc
    assert stored.status is S.PUBLISH_FAILED
    assert stored.last_error == "contract_invalid"
    assert not stored.publish.live


async def test_a_missing_contract_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    from blogwriter.publish.contract import ContractUnavailableError

    def missing(doc: Mapping[str, object], *, now: datetime | None = None) -> list[ContractError]:
        raise ContractUnavailableError("no contracts directory")

    monkeypatch.setattr(published_module, "validate_published_doc", missing)
    harness = make_harness()
    article = await seed_article(harness.services)
    approved = await post(harness, article.id, "approve", {})
    harness.services.clock.set(MONDAY)
    job = await harness.run_job(approved["jobId"])
    assert job.error is not None
    assert job.error.code == "contract_unavailable"
    assert await harness.services.store.find("published-blogs") == []


def test_the_job_types_and_steps_are_the_contracts_and_never_scheduled() -> None:
    registry = JobRegistry()
    register_jobs(registry)
    for job_type, steps in (
        (PUBLISH_ARTICLE, PUBLISH_STEPS),
        (PUBLISH_UNPUBLISH, UNPUBLISH_STEPS),
        (PUBLISH_STAGING, STAGING_STEPS),
    ):
        registered = registry.get(job_type)
        assert registered is not None
        assert registered.step_names == steps
        assert generates_content(job_type)
        assert job_type not in SCHEDULED_JOB_TYPES
    assert registry.names() == tuple(sorted((PUBLISH_ARTICLE, PUBLISH_STAGING, PUBLISH_UNPUBLISH)))


class LostResponseStore(DelegatingStore):
    """The first published-blogs create is applied, then its response is lost (a crash or a
    timeout right after the write)."""

    def __init__(self, inner: Store) -> None:
        super().__init__(inner)
        self.lost = 0

    @override
    async def create(self, container: str, body: Mapping[str, Any]) -> Doc:
        created = await super().create(container, body)
        if container == "published-blogs" and not self.lost:
            self.lost += 1
            raise StoreUnavailable("response lost")
        return created


class RacingStore(DelegatingStore):
    """Another writer takes the slug just before this article's first create."""

    @override
    async def create(self, container: str, body: Mapping[str, Any]) -> Doc:
        if container == "published-blogs":
            slug = body["id"]
            await super().create(container, {"id": slug, "slug": slug, "title": "Theirs"})
        return await super().create(container, body)


async def test_a_lost_create_response_is_recognised_as_our_own_write() -> None:
    clock = FakeClock()
    store = LostResponseStore(MemoryStore())
    services = make_services(store=store, clock=clock)
    harness = make_harness(services)
    article = await seed_article(services)
    approved = await post(harness, article.id, "approve", {})
    clock.set(MONDAY)
    job = await harness.run_job(approved["jobId"])
    assert store.lost == 1
    assert job.status == "succeeded", job.error
    upsert = job.step("upsert")
    assert upsert is not None
    assert upsert.runs == 2


async def test_a_slug_taken_after_the_check_is_never_overwritten() -> None:
    clock = FakeClock()
    services = make_services(store=RacingStore(MemoryStore()), clock=clock)
    harness = make_harness(services)
    article = await seed_article(services)
    approved = await post(harness, article.id, "approve", {})
    clock.set(MONDAY)
    job = await harness.run_job(approved["jobId"])
    assert job.error is not None
    assert job.error.code == "slug_taken"
    theirs = (await services.store.read("published-blogs", "scope-every-tool-call")).body
    assert theirs["title"] == "Theirs"
    stored = (await read_doc(services.store, Article, article.id)).doc
    assert stored.status is S.PUBLISH_FAILED
    assert not stored.publish.live
