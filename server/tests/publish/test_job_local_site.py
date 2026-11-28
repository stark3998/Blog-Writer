"""``publish.article`` / ``publish.unpublish`` with the portfolio in this replica
(``BW_PUBLISH_SITE_UPSTREAM``): no GitHub build; the site is revalidated and read back."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from blogwriter.core.testing import asgi_client
from blogwriter.db import Store
from blogwriter.domain.articles import Article
from blogwriter.domain.docstore import read_doc
from blogwriter.domain.states import ArticleStatus
from blogwriter.publish.portfolio import PortfolioError
from blogwriter.publish.settings import PublishSettings
from blogwriter.publish.site import LocalSiteClient
from blogwriter.publish.testing import (
    PublishHarness,
    make_harness,
    make_publish_settings,
    owner_headers,
    publish_app,
    seed_article,
)

S = ArticleStatus
MONDAY = datetime(2026, 11, 30, 14, 0, tzinfo=UTC)
UPSTREAM = "http://127.0.0.1:3000"
SLUG = "scope-every-tool-call"


class FakeSite:
    """The portfolio server: renders pages from ``published-blogs`` as of its last
    revalidation (like a cache that only refreshes when asked)."""

    def __init__(self, store: Store) -> None:
        self.store = store
        self.pages: dict[str, tuple[int, str]] = {}
        self.revalidated: list[list[str]] = []
        self.refresh_on_revalidate = True
        self.revalidate_statuses: list[int] = []
        self.page_requests: list[str] = []
        self.internal_headers: list[str | None] = []

    async def handle(self, request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/_bw/revalidate":
            self.internal_headers.append(request.headers.get("x-bw-internal"))
            if request.headers.get("x-bw-internal") != "1":
                return httpx.Response(404)
            if self.revalidate_statuses:
                return httpx.Response(self.revalidate_statuses.pop(0))
            body: Any = json.loads(request.content)
            self.revalidated.append(list(body["slugs"]))
            if self.refresh_on_revalidate:
                await self.render()
            return httpx.Response(200, json={"revalidated": body["slugs"]})
        if request.method == "GET" and request.url.path.startswith("/blog/"):
            self.page_requests.append(str(request.url))
            status, page = self.pages.get(request.url.path[len("/blog/") :], (404, "Not Found"))
            return httpx.Response(status, text=page)
        return httpx.Response(404)

    async def render(self) -> None:
        self.pages = {}
        for doc in await self.store.find("published-blogs"):
            if doc.body.get("status") == "published":
                marker = doc.body["updatedAt"]
                self.pages[doc.id] = (
                    200,
                    f'<html><head><meta name="bw-updated-at" content="{marker}"></head></html>',
                )

    def factory(self) -> Callable[[PublishSettings], LocalSiteClient]:
        def build(settings: PublishSettings) -> LocalSiteClient:
            return LocalSiteClient.from_settings(
                settings, transport=httpx.MockTransport(self.handle)
            )

        return build


def local_settings(**overrides: object) -> PublishSettings:
    values: dict[str, object] = {
        "site_upstream": UPSTREAM,
        "portfolio_repository": None,
        "portfolio_workflow": None,
        "portfolio_token": None,
        "preview_workflow": None,
    }
    values.update(overrides)
    return make_publish_settings(**values)


_SITES: dict[int, FakeSite] = {}


@pytest.fixture
def harness() -> PublishHarness:
    holder: list[FakeSite] = []

    def site(settings: PublishSettings) -> LocalSiteClient:
        return holder[0].factory()(settings)

    built = make_harness(settings=local_settings(), site=site)
    holder.append(FakeSite(built.services.store))
    _SITES[id(built)] = holder[0]
    return built


def site_of(harness: PublishHarness) -> FakeSite:
    return _SITES[id(harness)]


async def approve(harness: PublishHarness, article_id: str) -> str:
    etag = (await read_doc(harness.services.store, Article, article_id)).etag
    async with asgi_client(publish_app(harness)) as client:
        response = await client.post(
            f"/api/articles/{article_id}/approve", json={}, headers=owner_headers(etag)
        )
    assert response.status_code == 200, response.text
    return str(response.json()["jobId"])


async def article_of(store: Store, article_id: str) -> Article:
    return (await read_doc(store, Article, article_id)).doc


async def test_publish_revalidates_the_site_and_verifies_without_github(
    harness: PublishHarness,
) -> None:
    services = harness.services
    article = await seed_article(services)
    job_id = await approve(harness, article.id)
    services.clock.set(MONDAY)
    job = await harness.run_job(job_id)
    assert job.status == "succeeded", job.error
    assert [step.status for step in job.steps] == ["succeeded"] * 7

    site = site_of(harness)
    assert site.revalidated == [[SLUG]]
    assert site.internal_headers == ["1"]
    assert site.page_requests
    assert all("bw-verify=" in url for url in site.page_requests)
    assert harness.portfolio.calls == []  # no GitHub at all
    assert harness.portfolio.site_requests == []  # nothing through the egress helper

    stored = await article_of(services.store, article.id)
    assert stored.status is S.PUBLISHED
    assert stored.publish.live
    assert stored.publish.url == f"https://site.test/blog/{SLUG}"
    assert stored.publish.portfolio_run_id is None


async def test_publish_waits_for_the_new_version_on_the_site(harness: PublishHarness) -> None:
    services = harness.services
    article = await seed_article(services)
    site = site_of(harness)
    site.refresh_on_revalidate = False  # the site keeps serving nothing new
    job_id = await approve(harness, article.id)
    services.clock.set(MONDAY)
    job = await harness.run_job(job_id)
    assert job.status == "failed"
    assert job.error is not None
    assert job.error.code == "verify_timeout"
    assert (await article_of(services.store, article.id)).status is S.PUBLISH_FAILED


async def test_unpublish_revalidates_and_waits_for_the_page_to_go(
    harness: PublishHarness,
) -> None:
    services = harness.services
    article = await seed_article(services)
    job_id = await approve(harness, article.id)
    services.clock.set(MONDAY)
    assert (await harness.run_job(job_id)).status == "succeeded"

    etag = (await read_doc(services.store, Article, article.id)).etag
    async with asgi_client(publish_app(harness)) as client:
        response = await client.post(
            f"/api/articles/{article.id}/unpublish", json={}, headers=owner_headers(etag)
        )
    assert response.status_code == 202, response.text
    job = await harness.run_job(str(response.json()["jobId"]))
    assert job.status == "succeeded", job.error
    assert site_of(harness).revalidated[-1] == [SLUG]
    assert SLUG not in site_of(harness).pages
    stored = await article_of(services.store, article.id)
    assert stored.status is S.UNPUBLISHED
    assert not stored.publish.live


# ---------------------------------------------------------------------------- the client


async def test_client_revalidate_sends_the_internal_header_and_slugs() -> None:
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={})

    async with LocalSiteClient(UPSTREAM, transport=httpx.MockTransport(handle)) as client:
        assert await client.revalidate(["a", "b"]) == ["a", "b"]
    assert seen[0].url == "http://127.0.0.1:3000/_bw/revalidate"
    assert seen[0].headers["x-bw-internal"] == "1"
    assert json.loads(seen[0].content) == {"slugs": ["a", "b"]}


@pytest.mark.parametrize(
    ("status", "code", "transient"), [(503, "unavailable", True), (404, "unexpected_status", False)]
)
async def test_client_revalidate_errors(status: int, code: str, transient: bool) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(status))
    async with LocalSiteClient(UPSTREAM, transport=transport) as client:
        with pytest.raises(PortfolioError) as caught:
            await client.revalidate(["a"])
    assert (caught.value.code, caught.value.transient) == (code, transient)


async def test_client_unreachable_site() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    async with LocalSiteClient(UPSTREAM, transport=httpx.MockTransport(refuse)) as client:
        with pytest.raises(PortfolioError) as caught:
            await client.revalidate(["a"])
        assert caught.value.transient
        assert await client.page("/blog/a") is None


async def test_client_reads_pages_and_refuses_other_hosts() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text="page"))
    async with LocalSiteClient(UPSTREAM, transport=transport) as client:
        assert await client.page("/blog/a?bw-verify=1") == (200, b"page")
        with pytest.raises(ValueError, match="site-relative"):
            await client.page("//evil.test/x")
    with pytest.raises(ValueError, match="loopback"):
        LocalSiteClient("http://10.0.0.5:3000")


def test_local_site_settings_need_only_the_site_origin() -> None:
    settings = local_settings()
    assert settings.local_site
    assert settings.portfolio_configured
    assert local_settings(site_origin=None).missing_for_publish() == ["BW_PUBLISH_SITE_ORIGIN"]
    with pytest.raises(ValueError, match="loopback"):
        local_settings(site_upstream="http://example.com:3000")
