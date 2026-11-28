"""``fetch_page``: Web IQ Browse (``httpx.MockTransport``), the egress fetcher (``StaticHttp``),
the fallback between them, the URL allowlist and HTML-to-text (golden)."""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable, Coroutine, Mapping
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from blogwriter.core.egress import (
    EgressBlockedError,
    EgressError,
    EgressRedirectError,
    EgressResponse,
    EgressTimeoutError,
    EgressTooLargeError,
)
from blogwriter.domain.ports import FetchedPage, FetchRefused, PageFetcher
from blogwriter.domain.testing import StaticHttp
from blogwriter.research.fetch import (
    EgressFetcher,
    FallbackFetcher,
    RateLimiter,
    WebIqFetcher,
    check_fetch_url,
    default_query,
    fetch_page,
    html_to_text,
    plain_text,
)

GOLDENS = Path(__file__).parent / "goldens"
URL = "https://learn.microsoft.com/en-us/entra/workload-id/agent-identities"
OTHER = "https://www.example.org/research/agent-identity-survey-2026"
BROWSE = "https://api.microsoft.ai/v3/browse"
NOW = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)
PASSAGE = "Agent identities are a distinct object type. " * 10

type Handler = Callable[[httpx.Request], Coroutine[None, None, httpx.Response]]


def _page_fetcher_is_a_port(
    fetcher: FallbackFetcher, web: WebIqFetcher, egress: EgressFetcher
) -> tuple[PageFetcher, ...]:
    return fetcher, web, egress


def test_the_fetchers_satisfy_the_port() -> None:
    assert callable(_page_fetcher_is_a_port)


class Recorder:
    """A Browse API double: answers from a list, records requests."""

    def __init__(self, *responses: httpx.Response) -> None:
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]


def ok(content: str = PASSAGE, **fields: object) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "url": URL,
            "title": "Agent identities | Microsoft Learn",
            "content": content,
            "isAdult": False,
            "crawledAt": "2026-11-20T07:44:00Z",
            "traceId": "6ae4fd995e3d425f847c07c2f0839e38",
            **fields,
        },
    )


def error(status: int, code: str, retry_after: str | None = None) -> httpx.Response:
    return httpx.Response(
        status,
        json={
            "errorCode": code,
            "errorCategory": "UserError",
            "userMessage": "message",
            "retryAfter": retry_after,
            "traceId": "t",
        },
    )


class Sleeps:
    def __init__(self) -> None:
        self.seconds: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.seconds.append(seconds)


def webiq(
    handler: Handler | Recorder,
    *,
    token: Callable[[], Awaitable[str]] | None = None,
    timeout: float = 15.0,
    passage_max_chars: int = 3000,
    sleep: Sleeps | None = None,
) -> WebIqFetcher:
    async def fixed_token() -> str:
        return "test-token"

    return WebIqFetcher(
        browse_url=BROWSE,
        token=token or fixed_token,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        qps=1000,
        timeout=timeout,
        passage_max_chars=passage_max_chars,
        now=lambda: NOW,
        sleep=sleep or Sleeps(),
        limiter=RateLimiter(1000, sleep=Sleeps()),  # its waits are not the crawl polls
    )


# ---------------------------------------------------------------------------------------------
# Allowed URLs
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "allowed"),
    [
        (URL, ()),
        (URL, (OTHER,)),
        (URL + "?utm_source=x", (URL,)),
        ("http://learn.microsoft.com/a", ("http://learn.microsoft.com/a",)),
        ("https://user:pw@example.org/a", ("https://user:pw@example.org/a",)),
    ],
)
def test_only_allowed_https_urls_pass(url: str, allowed: tuple[str, ...]) -> None:
    with pytest.raises(FetchRefused) as caught:
        check_fetch_url(url, allowed)
    assert caught.value.reason == "url_not_allowed"


def test_an_allowed_https_url_passes() -> None:
    check_fetch_url(URL, [URL])


def test_default_query_comes_from_the_path() -> None:
    assert default_query(URL) == "entra workload id agent identities"
    assert default_query("https://nist.gov/") == "nist.gov"
    assert default_query("https://example.org/2026/11/index.html") == "example.org"


# ---------------------------------------------------------------------------------------------
# Web IQ Browse
# ---------------------------------------------------------------------------------------------


async def test_browse_sends_the_documented_request() -> None:
    api = Recorder(ok())
    fetcher = webiq(api)

    page = await fetcher.fetch(URL, allowed_urls=[URL], query="Agent identities in Microsoft Entra")

    assert page == FetchedPage(
        url=URL,
        title="Agent identities | Microsoft Learn",
        text=PASSAGE.strip(),
        via="webiq",
        fetched_at=NOW,
    )
    (request,) = api.requests
    assert request.method == "POST"
    assert str(request.url) == BROWSE
    assert request.headers["authorization"] == "Bearer test-token"
    assert request.headers["content-type"] == "application/json"
    golden = json.loads((GOLDENS / "webiq_browse_request.json").read_text(encoding="utf-8"))
    assert json.loads(request.content) == golden


async def test_browse_derives_a_query_and_caps_the_passage() -> None:
    api = Recorder(ok(content="word " * 2000))
    fetcher = webiq(api, passage_max_chars=500)
    page = await fetch_page(fetcher, URL, allowed_urls=[URL])
    assert len(page.text) <= 500
    assert json.loads(api.requests[0].content)["query"] == "entra workload id agent identities"
    assert json.loads(api.requests[0].content)["maxLength"] == 500


async def test_a_pending_crawl_is_polled_after_retry_after() -> None:
    api = Recorder(
        httpx.Response(202, json={"url": URL, "retryAfter": "2026-11-26T12:00:02Z"}), ok()
    )
    sleeps = Sleeps()
    page = await webiq(api, sleep=sleeps).fetch(URL, allowed_urls={URL})
    assert page.via == "webiq"
    assert sleeps.seconds == [2.0]
    assert len(api.requests) == 2


async def test_a_crawl_pending_beyond_the_budget_is_unavailable() -> None:
    api = Recorder(httpx.Response(202, json={"url": URL, "retryAfter": "2026-11-26T13:00:00Z"}))
    with pytest.raises(FetchRefused) as caught:
        await webiq(api).fetch(URL, allowed_urls={URL})
    assert caught.value.reason == "unavailable"
    assert len(api.requests) == 1


@pytest.mark.parametrize(
    ("response", "reason"),
    [
        (error(401, "AuthNoToken"), "unavailable"),
        (error(401, "AuthUnauthorizedEntryId"), "unavailable"),
        (error(403, "AuthUnauthorizedService"), "unavailable"),
        (error(403, "URLFilteredOutEncumbrance", "2026-11-27T12:00:00Z"), "blocked"),
        (error(403, "URLFilteredOutSpamAdult"), "blocked"),
        (error(403, "BrowseApiIsUrlExcluded"), "blocked"),
        (error(400, "InvalidUrl"), "unsupported_type"),
        (error(400, "NotSupported"), "unsupported_type"),
        (error(400, "HandlerInvalidInput"), "http_error"),
        (error(404, "DocNotFound"), "http_error"),
        (error(429, "AuthUserThrottled", "60s"), "unavailable"),
        (error(430, "BrowseApiTooManyOnDemandCrawls"), "unavailable"),
        (error(500, "InternalServerError", "30s"), "unavailable"),
        (error(520, "FailedNoContent"), "unavailable"),
        (error(524, "HostCrawlTimeout"), "unavailable"),
        (httpx.Response(503, text="<html>maintenance</html>"), "unavailable"),
        (httpx.Response(302, headers={"location": "https://elsewhere.example/"}), "http_error"),
        (ok(isAdult=True), "blocked"),
        (ok(content="   "), "http_error"),
        (httpx.Response(200, text="not json"), "http_error"),
    ],
)
async def test_browse_failures_map_to_reasons(response: httpx.Response, reason: str) -> None:
    with pytest.raises(FetchRefused) as caught:
        await webiq(Recorder(response)).fetch(URL, allowed_urls={URL})
    assert caught.value.reason == reason


async def test_transport_errors_timeouts_and_token_failures() -> None:
    async def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    with pytest.raises(FetchRefused) as unreachable:
        await webiq(refuse).fetch(URL, allowed_urls={URL})
    assert unreachable.value.reason == "unavailable"

    async def slow(request: httpx.Request) -> httpx.Response:
        import asyncio

        await asyncio.sleep(1)
        return ok()

    with pytest.raises(FetchRefused) as late:
        await webiq(slow, timeout=0.05).fetch(URL, allowed_urls={URL})
    assert late.value.reason == "timeout"

    async def broken_token() -> str:
        raise RuntimeError("identity endpoint unreachable")

    api = Recorder(ok())
    with pytest.raises(FetchRefused) as no_token:
        await webiq(api, token=broken_token).fetch(URL, allowed_urls={URL})
    assert no_token.value.reason == "unavailable"
    assert api.requests == []


async def test_browse_refuses_urls_before_any_request(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    api = Recorder(ok())
    with pytest.raises(FetchRefused) as caught:
        await webiq(api).fetch(URL, allowed_urls=[OTHER])
    assert caught.value.reason == "url_not_allowed"
    assert api.requests == []


async def test_browse_logs_carry_no_url_token_or_text(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    await webiq(Recorder(ok())).fetch(URL, allowed_urls={URL}, query="secret topic words")
    with pytest.raises(FetchRefused):
        await webiq(Recorder(error(403, "URLFilteredOutEncumbrance"))).fetch(
            URL, allowed_urls={URL}
        )
    lines = [record.getMessage() + repr(record.__dict__) for record in caplog.records]
    assert any(
        "research_fetch_failed" in line and "URLFilteredOutEncumbrance" in line for line in lines
    )
    for line in lines:
        assert URL not in line
        assert "test-token" not in line
        assert "secret topic words" not in line
        assert "distinct object type" not in line


async def test_the_rate_limiter_spaces_calls() -> None:
    clock = [0.0]
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)
        clock[0] += seconds

    limiter = RateLimiter(4, monotonic=lambda: clock[0], sleep=sleep)
    for _ in range(3):
        await limiter.acquire()
    assert slept == [0.25, 0.25]
    clock[0] += 10
    await limiter.acquire()
    assert slept == [0.25, 0.25]
    with pytest.raises(ValueError, match="positive"):
        RateLimiter(0)


# ---------------------------------------------------------------------------------------------
# Egress fetcher and HTML to text
# ---------------------------------------------------------------------------------------------


def test_html_to_text_golden() -> None:
    raw = (GOLDENS / "article_page.html").read_bytes()
    title, text = html_to_text(raw, charset="utf-8", max_chars=3000)
    assert title == "Agent identities in Microsoft Entra | Example Docs"
    assert text == (GOLDENS / "article_page.txt").read_text(encoding="utf-8").rstrip("\n")
    for boilerplate in ("cookies", "Products", "Quickstart", "Share this", "Hidden", "Related"):
        assert boilerplate not in text


def test_html_to_text_falls_back_to_the_body_and_the_og_title() -> None:
    html = (
        b'<html><head><meta property="og:title" content="OG title"></head>'
        b"<body><div>First block</div><div>Second <i>block</i></div></body></html>"
    )
    assert html_to_text(html, charset=None, max_chars=100) == (
        "OG title",
        "First block\nSecond block",
    )
    article = (
        b"<body><article><p>short</p></article><article><p>the longer article</p></article></body>"
    )
    assert (
        html_to_text(article, charset="no-such-charset", max_chars=100)[1] == "the longer article"
    )


def test_html_to_text_cuts_at_a_word_and_refuses_xml() -> None:
    html = b"<p>" + b"alpha beta gamma " * 50 + b"</p>"
    _, text = html_to_text(html, charset="utf-8", max_chars=40)
    assert len(text) <= 40
    assert not text.endswith(" ")
    assert text.split()[-1] in {"alpha", "beta", "gamma"}
    with pytest.raises(ValueError, match="XML"):
        html_to_text(
            b'<?xml version="1.0"?><rss><channel></channel></rss>', charset=None, max_chars=10
        )


def test_plain_text_is_normalized() -> None:
    assert plain_text(b"line one\r\n\r\n  line\ttwo \x00", charset="latin-1", max_chars=50) == (
        "line one\nline two"
    )


async def test_egress_fetcher_reads_html_and_plain_text() -> None:
    http = StaticHttp()
    http.add(
        URL, (GOLDENS / "article_page.html").read_bytes(), content_type="text/html; charset=utf-8"
    )
    http.add(OTHER, b"Plain notes about agents.", content_type="text/plain")
    fetcher = EgressFetcher(egress=http, passage_max_chars=3000, now=lambda: NOW)

    page = await fetcher.fetch(URL, allowed_urls=[URL, OTHER])
    assert page.via == "egress"
    assert page.fetched_at == NOW
    assert page.title == "Agent identities in Microsoft Entra | Example Docs"
    assert page.text.startswith("Agent identities\nPublished October 14, 2026")
    plain = await fetcher.fetch(OTHER, allowed_urls=[URL, OTHER])
    assert plain.text == "Plain notes about agents."
    assert plain.title is None
    assert http.requested == [URL, OTHER]


class Raising:
    def __init__(self, exc: Exception) -> None:
        self.exc = exc

    async def get(
        self,
        url: str,
        *,
        allow_http: bool = False,
        headers: Mapping[str, str] | None = None,
        max_bytes: int | None = None,
    ) -> EgressResponse:
        raise self.exc


@pytest.mark.parametrize(
    ("exc", "reason"),
    [
        (EgressBlockedError("address_private"), "blocked"),
        (EgressTooLargeError("too_large"), "too_large"),
        (EgressTimeoutError("timeout"), "timeout"),
        (EgressRedirectError("too_many_redirects"), "http_error"),
        (EgressError("transport_error"), "http_error"),
    ],
)
async def test_egress_errors_map_to_reasons(exc: Exception, reason: str) -> None:
    with pytest.raises(FetchRefused) as caught:
        await EgressFetcher(egress=Raising(exc)).fetch(URL, allowed_urls=[URL])
    assert caught.value.reason == reason


@pytest.mark.parametrize(
    ("status", "content_type", "content", "reason"),
    [
        (404, "text/html", b"<p>gone</p>", "http_error"),
        (200, "application/pdf", b"%PDF-1.7", "unsupported_type"),
        (200, "application/rss+xml", b"<rss/>", "unsupported_type"),
        (200, "text/html", b'<?xml version="1.0"?><feed></feed>', "unsupported_type"),
        (200, "text/html", b"<html><body><nav>only nav</nav></body></html>", "unsupported_type"),
    ],
)
async def test_egress_responses_without_usable_text_are_refused(
    status: int, content_type: str, content: bytes, reason: str
) -> None:
    http = StaticHttp()
    http.add(URL, content, status=status, content_type=content_type)
    with pytest.raises(FetchRefused) as caught:
        await EgressFetcher(egress=http).fetch(URL, allowed_urls=[URL])
    assert caught.value.reason == reason


async def test_egress_fetcher_without_egress_is_unavailable() -> None:
    def missing() -> StaticHttp:
        raise LookupError("egress is not set")

    with pytest.raises(FetchRefused) as caught:
        await EgressFetcher(resolve=missing).fetch(URL, allowed_urls=[URL])
    assert caught.value.reason == "unavailable"
    with pytest.raises(ValueError, match="exactly one"):
        EgressFetcher()


async def test_egress_refuses_urls_it_was_not_given() -> None:
    http = StaticHttp()
    with pytest.raises(FetchRefused) as caught:
        await EgressFetcher(egress=http).fetch(URL, allowed_urls=[])
    assert caught.value.reason == "url_not_allowed"
    assert http.requested == []


# ---------------------------------------------------------------------------------------------
# Fallback
# ---------------------------------------------------------------------------------------------


def egress_with_page() -> tuple[EgressFetcher, StaticHttp]:
    http = StaticHttp()
    http.add(URL, b"<main><p>Egress copy of the page.</p></main>", content_type="text/html")
    return EgressFetcher(egress=http), http


async def test_fallback_prefers_web_iq() -> None:
    egress, http = egress_with_page()
    fetcher = FallbackFetcher(webiq(Recorder(ok())), egress)
    page = await fetcher.fetch(URL, allowed_urls=[URL], query="agents")
    assert page.via == "webiq"
    assert http.requested == []
    assert fetcher.webiq_enabled
    assert fetcher.state == "on"


async def test_fallback_uses_egress_when_web_iq_fails() -> None:
    egress, http = egress_with_page()
    fetcher = FallbackFetcher(webiq(Recorder(error(503, "InternalServerError"))), egress)
    page = await fetcher.fetch(URL, allowed_urls=[URL])
    assert page.via == "egress"
    assert page.text == "Egress copy of the page."
    assert http.requested == [URL]


async def test_fallback_does_not_fetch_around_a_policy_refusal() -> None:
    egress, http = egress_with_page()
    fetcher = FallbackFetcher(webiq(Recorder(error(403, "URLFilteredOutEncumbrance"))), egress)
    with pytest.raises(FetchRefused) as caught:
        await fetcher.fetch(URL, allowed_urls=[URL])
    assert caught.value.reason == "blocked"
    assert http.requested == []


async def test_fallback_checks_the_url_before_either_service() -> None:
    egress, http = egress_with_page()
    api = Recorder(ok())
    with pytest.raises(FetchRefused):
        await FallbackFetcher(webiq(api), egress).fetch(URL, allowed_urls=[OTHER])
    assert api.requests == []
    assert http.requested == []


async def test_egress_only_without_web_iq() -> None:
    egress, _ = egress_with_page()
    fetcher = FallbackFetcher(None, egress)
    assert not fetcher.webiq_enabled
    assert fetcher.state == "off"
    assert (await fetcher.fetch(URL, allowed_urls=[URL])).via == "egress"


async def test_repeated_outages_open_the_circuit_then_one_probe_closes_it() -> None:
    clock = [0.0]
    api = Recorder(error(503, "InternalServerError"))
    egress, http = egress_with_page()
    fetcher = FallbackFetcher(
        webiq(api), egress, failures_to_open=2, cooldown_seconds=60, monotonic=lambda: clock[0]
    )
    for _ in range(4):
        assert (await fetcher.fetch(URL, allowed_urls=[URL])).via == "egress"
    assert len(api.requests) == 2  # the circuit opened after two outages
    assert fetcher.state == "degraded"
    clock[0] += 61
    api.responses = [ok()]
    assert (await fetcher.fetch(URL, allowed_urls=[URL])).via == "webiq"
    assert fetcher.state == "on"
    assert len(http.requested) == 4


async def test_fetch_page_passes_the_query_only_to_research_fetchers() -> None:
    from blogwriter.domain.testing import StaticPageFetcher

    static = StaticPageFetcher({URL: "text"})
    assert (await fetch_page(static, URL, allowed_urls=[URL], query="ignored")).text == "text"


async def test_egress_sniffs_html_without_a_content_type() -> None:
    http = StaticHttp()
    http.responses[URL] = EgressResponse(
        url=URL, status=200, content_type=None, content=b"<html><body><p>Sniffed.</p></body></html>"
    )
    http.responses[OTHER] = EgressResponse(
        url=OTHER, status=200, content_type=None, content=b"\x89PNG\r\n\x1a\n"
    )
    fetcher = EgressFetcher(egress=http)
    assert (await fetcher.fetch(URL, allowed_urls=[URL, OTHER])).text == "Sniffed."
    with pytest.raises(FetchRefused) as caught:
        await fetcher.fetch(OTHER, allowed_urls=[URL, OTHER])
    assert caught.value.reason == "unsupported_type"
