"""``fetch_page``: Web IQ Browse first, the egress helper as fallback. Passages stay in memory.

Every fetcher here satisfies :class:`blogwriter.domain.ports.PageFetcher` and refuses
(:class:`~blogwriter.domain.ports.FetchRefused` ``url_not_allowed``) any URL that is not in
``allowed_urls`` (this run's search sources, owner-typed URLs, or a brief's citations at gate
time) or is not ``https``. Nothing adds a query string: the URL goes out exactly as allowed.

:class:`WebIqFetcher`
    ``POST <endpoint>/v3/browse`` with a managed-identity bearer token (scope
    ``BW_RESEARCH_WEBIQ_SCOPE``), ``contentFormat: passage`` (a query-focused extract of at most
    3,000 characters, the only format without full document access), ``liveCrawl: fallback``. A
    ``202`` (live crawl pending) is polled again after its ``retryAfter`` within the fetch budget.
    Calls go through a client-side rate limiter (``BW_RESEARCH_WEBIQ_QPS``), on a dedicated
    ``httpx.AsyncClient`` without redirects, proxies or environment settings.
:class:`EgressFetcher`
    ``services.egress`` (SSRF-safe) plus HTML-to-text in a worker thread: BeautifulSoup over
    lxml, boilerplate removed, the main content only, at most ``PASSAGE_MAX_CHARS``.
:class:`FallbackFetcher`
    Tries Web IQ, then egress; :attr:`FetchedPage.via` says which one served. A page Web IQ
    refuses by policy (adult or spam filtering, robots or paywall rules, the source's own 403)
    is not fetched around: that refusal stands. After repeated Web IQ outages a circuit breaker
    sends fetches straight to egress for a few minutes.

Web IQ Browse accepts an optional ``query`` that ranks the passages; the port has no such
parameter, so these fetchers take it as an extra keyword (:func:`fetch_page` passes it when the
fetcher supports it) and derive one from the URL otherwise.

Logs carry reasons, statuses, durations and the service that served. Never a URL, a query, a
title, a token or fetched text.
"""

from __future__ import annotations

import abc
import asyncio
import codecs
import logging
import re
import time
import unicodedata
from collections.abc import Awaitable, Callable, Collection
from datetime import UTC, datetime
from typing import Final, Literal, cast
from urllib.parse import urlsplit

import httpx
from bs4 import BeautifulSoup
from bs4.element import NavigableString, Tag

from blogwriter.core.egress import (
    EgressBlockedError,
    EgressError,
    EgressTimeoutError,
    EgressTooLargeError,
)
from blogwriter.core.logging import describe_exception, register_log_fields
from blogwriter.domain.base import check_https_url
from blogwriter.domain.ports import FetchedPage, FetchRefused, HttpGetter, PageFetcher

__all__ = [
    "FETCH_REASONS",
    "EgressFetcher",
    "FallbackFetcher",
    "RateLimiter",
    "ResearchFetcher",
    "TokenSource",
    "WebIqFetcher",
    "check_fetch_url",
    "default_query",
    "fetch_page",
    "html_to_text",
    "plain_text",
]

type TokenSource = Callable[[], Awaitable[str]]
"""An async callable returning a bearer token (``azure.identity.aio.get_bearer_token_provider``)."""

FETCH_REASONS: Final = frozenset(
    {"url_not_allowed", "blocked", "too_large", "timeout", "http_error", "unsupported_type"}
    | {"unavailable"}
)
"""The :class:`FetchRefused` reasons this module raises (the port's list)."""

WEBIQ_MAX_QUERY_CHARS: Final = 1000
TITLE_MAX_CHARS: Final = 500
_TERMINAL_REASONS: Final = frozenset({"url_not_allowed", "blocked"})
"""Refusals the fallback must not fetch around."""
_OUTAGE_REASONS: Final = frozenset({"unavailable", "timeout"})
"""Web IQ failures that count towards the circuit breaker (the service, not the page)."""

_logger: Final = logging.getLogger("blogwriter.research.fetch")
register_log_fields("fetch_via", "fetch_reason", "webiq_code")

_WEBIQ_CODE_RE: Final = re.compile(r"[A-Za-z][A-Za-z0-9]{0,63}")
_WEBIQ_FILTERED: Final = frozenset(
    {
        "URLFilteredOutSpamAdult",
        "URLFilteredOutEncumbrance",
        "URLFilteredOutSource403Forbidden",
        "BrowseApiIsFilteredOut",
        "BrowseApiIsUrlExcluded",
    }
)
_WEBIQ_UNSUPPORTED: Final = frozenset({"InvalidUrl", "NotSupported"})
_DURATION_RE: Final = re.compile(r"^(\d{1,6})(?:\.\d+)?s$")


def _utc_now() -> datetime:
    return datetime.now(UTC)


# --------------------------------------------------------------------------------------------
# Shared rules
# --------------------------------------------------------------------------------------------


def check_fetch_url(url: str, allowed_urls: Collection[str]) -> None:
    """Refuse (``url_not_allowed``) a URL outside ``allowed_urls`` or one that is not https."""
    if url not in allowed_urls:
        raise FetchRefused("url_not_allowed")
    try:
        check_https_url(url)
    except ValueError:
        raise FetchRefused("url_not_allowed") from None


_QUERY_WORD_RE: Final = re.compile(r"[A-Za-z][A-Za-z0-9]*")
_QUERY_STOPWORDS: Final = frozenset(
    {"html", "htm", "php", "aspx", "asp", "jsp", "index", "en", "us", "www", "amp"}
)


def default_query(url: str) -> str:
    """A Browse ``query`` derived from the URL path (``.../conditional-access/overview`` ->
    ``conditional access overview``), or the host name for a bare domain."""
    parts = urlsplit(url)
    words = [
        word
        for word in _QUERY_WORD_RE.findall(parts.path)
        if word.lower() not in _QUERY_STOPWORDS and len(word) < 40
    ]
    text = " ".join(words[-12:])
    return text or (parts.hostname or "web page")


def _clean_line(text: str) -> str:
    """Whitespace collapsed, control and format characters removed."""
    kept = [
        char for char in text if not unicodedata.category(char).startswith("C") or char in " \t"
    ]
    return " ".join("".join(kept).split())


def _normalize_text(text: str) -> str:
    lines = (_clean_line(line) for line in text.splitlines())
    return "\n".join(line for line in lines if line)


def _truncate(text: str, max_chars: int) -> str:
    """At most ``max_chars``, cut at a word boundary when one is near the end."""
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    boundary = max(cut.rfind(" "), cut.rfind("\n"))
    if boundary >= max_chars * 0.8:
        cut = cut[:boundary]
    return cut.rstrip()


def _clean_title(title: object) -> str | None:
    if not isinstance(title, str):
        return None
    text = _clean_line(title)
    return _truncate(text, TITLE_MAX_CHARS) or None


# --------------------------------------------------------------------------------------------
# HTML to text (runs in a worker thread)
# --------------------------------------------------------------------------------------------

_DROP_TAGS: Final = frozenset(
    {"script", "style", "noscript", "template", "svg", "canvas", "iframe", "object", "embed"}
    | {"form", "button", "input", "select", "textarea", "nav", "header", "footer", "aside"}
    | {"dialog", "menu", "picture", "video", "audio", "map"}
)
_BLOCK_TAGS: Final = (
    "p",
    "div",
    "section",
    "article",
    "main",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "li",
    "ul",
    "ol",
    "pre",
    "blockquote",
    "table",
    "tr",
    "dd",
    "dt",
    "dl",
    "figcaption",
    "summary",
    "details",
    "address",
    "hr",
)
_BOILERPLATE_ROLES: Final = frozenset(
    {"navigation", "banner", "contentinfo", "complementary", "search", "dialog", "alert"}
    | {"menu", "menubar", "toolbar", "alertdialog"}
)
_BOILERPLATE_TOKENS: Final = frozenset(
    {"cookie", "cookies", "consent", "newsletter", "subscribe", "share", "sharing", "social"}
    | {"related", "comments", "comment", "sidebar", "breadcrumb", "breadcrumbs", "advert"}
    | {"advertisement", "ads", "promo", "popup", "modal", "skip-link", "navbar", "toc"}
)
_BREAK: Final = "\u2029"
"""Marks block boundaries while the HTML tree is flattened (PARAGRAPH SEPARATOR)."""
_XML_DECLARATION: Final = re.compile(rb"^(?:\xef\xbb\xbf)?\s*<\?xml", re.IGNORECASE)
_HTML_HINT: Final = re.compile(rb"<\s*(?:html|head|body|p|div|article|main)\b", re.IGNORECASE)


def _attr_tokens(tag: Tag, name: str) -> list[str]:
    value = tag.get(name)
    if value is None:
        return []
    raw = " ".join(value) if isinstance(value, list) else str(value)
    return raw.lower().split()


def _is_boilerplate(tag: Tag) -> bool:
    if tag.name in _DROP_TAGS:
        return True
    role = tag.get("role")
    if isinstance(role, str) and role.strip().lower() in _BOILERPLATE_ROLES:
        return True
    if str(tag.get("aria-hidden", "")).strip().lower() == "true" or tag.has_attr("hidden"):
        return True
    tokens = [*_attr_tokens(tag, "class"), *_attr_tokens(tag, "id")]
    return any(token in _BOILERPLATE_TOKENS for token in tokens)


def _main_root(soup: BeautifulSoup) -> Tag:
    main = soup.find("main")
    if main is not None:
        return main
    articles = list(soup.find_all("article"))
    if articles:
        return max(articles, key=lambda tag: len(tag.get_text()))
    role_main = soup.find(role="main")
    if role_main is not None:
        return role_main
    body = soup.find("body")
    if body is not None:
        return body
    return soup


def _decode_charset(charset: str | None) -> str | None:
    if charset is None:
        return None
    try:
        return codecs.lookup(charset).name
    except LookupError:
        return None


def html_to_text(content: bytes, *, charset: str | None, max_chars: int) -> tuple[str | None, str]:
    """The page title and main text of an HTML document (blocking: call it in a thread).

    Scripts, styles, navigation, headers, footers, asides, forms and common boilerplate
    (cookie banners, share bars, related links) are removed; the ``<main>`` element, else the
    longest ``<article>``, else ``role=main``, else the body is kept. Blocks become lines,
    whitespace is collapsed and the text is cut at ``max_chars``. ``ValueError`` for XML that is
    not HTML.
    """
    head = content[:8192]
    if _XML_DECLARATION.match(head) and not _HTML_HINT.search(head):
        raise ValueError("the document is XML, not HTML")
    soup = BeautifulSoup(content, "lxml", from_encoding=_decode_charset(charset))
    title: str | None = None
    if soup.title is not None:
        title = _clean_title(soup.title.get_text(" "))
    if title is None:
        meta = soup.find("meta", property="og:title")
        if meta is not None:
            title = _clean_title(meta.get("content"))
    head_tag = soup.find("head")
    if head_tag is not None:
        head_tag.decompose()
    for tag in list(soup.find_all(True)):
        if not tag.decomposed and _is_boilerplate(tag):
            tag.decompose()
    root = _main_root(soup)
    for br in list(root.find_all("br")):
        br.replace_with(NavigableString(_BREAK))
    for block in list(root.find_all(_BLOCK_TAGS)):
        block.insert_before(NavigableString(_BREAK))
        block.insert_after(NavigableString(_BREAK))
    # Source newlines are just whitespace in HTML: only block boundaries become lines.
    lines = (_clean_line(part) for part in root.get_text().split(_BREAK))
    return title, _truncate("\n".join(line for line in lines if line), max_chars)


def plain_text(content: bytes, *, charset: str | None, max_chars: int) -> str:
    """A ``text/plain`` body as normalized text (at most ``max_chars``)."""
    encoding = _decode_charset(charset) or "utf-8"
    return _truncate(_normalize_text(content.decode(encoding, errors="replace")), max_chars)


def _looks_like_html(content: bytes) -> bool:
    return bool(_HTML_HINT.search(content[:8192]))


# --------------------------------------------------------------------------------------------
# Rate limiting
# --------------------------------------------------------------------------------------------


class RateLimiter:
    """At most ``rate`` acquisitions per second, spaced evenly (no bursts)."""

    def __init__(
        self,
        rate: float,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if rate <= 0:
            raise ValueError("rate must be positive")
        self._interval = 1.0 / rate
        self._monotonic = monotonic
        self._sleep = sleep
        self._next = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = self._monotonic()
            wait = self._next - now
            if wait > 0:
                await self._sleep(wait)
                now = self._next
            self._next = max(now, self._next) + self._interval


# --------------------------------------------------------------------------------------------
# Fetchers
# --------------------------------------------------------------------------------------------


class ResearchFetcher(abc.ABC):
    """Base of the research fetchers: a :class:`PageFetcher` that also takes a Browse query."""

    @abc.abstractmethod
    async def fetch(
        self, url: str, *, allowed_urls: Collection[str], query: str | None = None
    ) -> FetchedPage:
        """Fetch ``url`` (only if allowed) and return its passage, in memory only."""


async def fetch_page(
    fetcher: PageFetcher, url: str, *, allowed_urls: Collection[str], query: str | None = None
) -> FetchedPage:
    """``fetcher.fetch`` with the Browse ``query`` when the fetcher takes one."""
    if isinstance(fetcher, ResearchFetcher):
        return await fetcher.fetch(url, allowed_urls=allowed_urls, query=query)
    return await fetcher.fetch(url, allowed_urls=allowed_urls)


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)


class _BrowseRefused(FetchRefused):
    """A Browse refusal with the HTTP status and Web IQ ``errorCode`` (for the log line)."""

    def __init__(self, reason: str, *, status: int | None = None, code: str | None = None) -> None:
        super().__init__(reason)
        self.status = status
        self.code = code


class WebIqFetcher(ResearchFetcher):
    """``fetch_page`` over the Web IQ Browse API (managed-identity bearer token)."""

    def __init__(
        self,
        *,
        browse_url: str,
        token: TokenSource,
        client: httpx.AsyncClient,
        qps: float = 15.0,
        timeout: float = 15.0,
        passage_max_chars: int = 3000,
        max_polls: int = 2,
        now: Callable[[], datetime] = _utc_now,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        limiter: RateLimiter | None = None,
    ) -> None:
        check_https_url(browse_url)
        if timeout <= 0 or passage_max_chars < 1 or max_polls < 0:
            raise ValueError("timeout, passage_max_chars and max_polls must be positive")
        self._browse_url = browse_url
        self._token = token
        self._client = client
        self._timeout = timeout
        self._max_chars = passage_max_chars
        self._max_polls = max_polls
        self._now = now
        self._sleep = sleep
        self._limiter = limiter if limiter is not None else RateLimiter(qps, sleep=sleep)

    def request_body(self, url: str, query: str | None) -> dict[str, object]:
        """The Browse request body for ``url`` (public: the URL and a short query)."""
        text = _clean_line(query or "") or default_query(url)
        return {
            "url": url,
            "query": text[:WEBIQ_MAX_QUERY_CHARS],
            "contentFormat": "passage",
            "maxLength": self._max_chars,
            "liveCrawl": "fallback",
            "language": "en",
            "region": "US",
        }

    async def fetch(
        self, url: str, *, allowed_urls: Collection[str], query: str | None = None
    ) -> FetchedPage:
        check_fetch_url(url, allowed_urls)
        body = self.request_body(url, query)
        started = time.perf_counter()
        try:
            async with asyncio.timeout(self._timeout):
                page = await self._browse(url, body)
        except TimeoutError:
            self._log_failure(_BrowseRefused("timeout"), started)
            raise FetchRefused("timeout") from None
        except FetchRefused as exc:
            self._log_failure(exc, started)
            raise FetchRefused(exc.reason) from None
        _logger.info(
            "research_fetch",
            extra={"fetch_via": "webiq", "duration_ms": _elapsed_ms(started)},
        )
        return page

    async def _browse(self, url: str, body: dict[str, object]) -> FetchedPage:
        for poll in range(self._max_polls + 1):
            await self._limiter.acquire()
            token = await self._bearer()
            try:
                response = await self._client.post(
                    self._browse_url,
                    json=body,
                    headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                )
            except httpx.TimeoutException:
                raise FetchRefused("timeout") from None
            except httpx.HTTPError:
                raise FetchRefused("unavailable") from None
            status = response.status_code
            if status == 200:
                return self._page(url, response)
            if status == 202:
                wait = self._retry_wait(response) if poll < self._max_polls else None
                if wait is None:
                    raise _BrowseRefused("unavailable", status=status)
                await self._sleep(wait)
                continue
            reason, code = self._reason(status, response)
            raise _BrowseRefused(reason, status=status, code=code)
        raise FetchRefused("unavailable")  # pragma: no cover - the loop always returns or raises

    async def _bearer(self) -> str:
        try:
            token = await self._token()
        except Exception as exc:
            _logger.warning(
                "research_webiq_token_failed",
                extra={"fetch_reason": "unavailable", **describe_exception(exc)},
            )
            raise FetchRefused("unavailable") from None
        if not token:
            raise FetchRefused("unavailable")
        return token

    def _page(self, url: str, response: httpx.Response) -> FetchedPage:
        try:
            data: object = response.json()
        except ValueError:
            raise FetchRefused("http_error") from None
        if not isinstance(data, dict):
            raise FetchRefused("http_error")
        body = cast(dict[str, object], data)
        if body.get("isAdult") is True:
            raise FetchRefused("blocked")
        content = body.get("content")
        if not isinstance(content, str):
            raise FetchRefused("http_error")
        text = _truncate(_normalize_text(content), self._max_chars)
        if not text:
            raise FetchRefused("http_error")
        return FetchedPage(
            url=url,
            title=_clean_title(body.get("title")),
            text=text,
            via="webiq",
            fetched_at=self._now(),
        )

    def _retry_wait(self, response: httpx.Response) -> float | None:
        """Seconds until a pending crawl may be polled (``None``: not within the budget)."""
        try:
            data: object = response.json()
        except ValueError:
            return None
        raw = cast(dict[str, object], data).get("retryAfter") if isinstance(data, dict) else None
        if not isinstance(raw, str):
            return None
        seconds: float
        duration = _DURATION_RE.fullmatch(raw.strip())
        if duration is not None:
            seconds = float(duration.group(1))
        else:
            try:
                moment = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
            except ValueError:
                return None
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=UTC)
            seconds = (moment - self._now()).total_seconds()
        seconds = max(seconds, 0.5)
        return seconds if seconds < self._timeout else None

    @staticmethod
    def _reason(status: int, response: httpx.Response) -> tuple[str, str | None]:
        code: str | None = None
        try:
            data: object = response.json()
        except ValueError:
            data = None
        if isinstance(data, dict):
            raw = cast(dict[str, object], data).get("errorCode")
            if isinstance(raw, str) and _WEBIQ_CODE_RE.fullmatch(raw):
                code = raw
        if code in _WEBIQ_FILTERED:
            return "blocked", code
        if code in _WEBIQ_UNSUPPORTED:
            return "unsupported_type", code
        if status in (401, 403, 429, 430) or status >= 500:
            return "unavailable", code
        return "http_error", code

    @staticmethod
    def _log_failure(exc: FetchRefused, started: float) -> None:
        extra: dict[str, object] = {
            "fetch_via": "webiq",
            "fetch_reason": exc.reason,
            "duration_ms": _elapsed_ms(started),
        }
        if isinstance(exc, _BrowseRefused):
            if exc.status is not None:
                extra["status"] = exc.status
            if exc.code is not None:
                extra["webiq_code"] = exc.code
        _logger.warning("research_fetch_failed", extra=extra)

    async def aclose(self) -> None:
        await self._client.aclose()


_HTML_TYPES: Final = frozenset({"text/html", "application/xhtml+xml"})
_ACCEPT: Final = "text/html,application/xhtml+xml;q=0.9,text/plain;q=0.8"


class EgressFetcher(ResearchFetcher):
    """``fetch_page`` over the egress helper plus local HTML-to-text (main text only)."""

    def __init__(
        self,
        *,
        egress: HttpGetter | None = None,
        resolve: Callable[[], HttpGetter] | None = None,
        timeout: float = 15.0,
        passage_max_chars: int = 3000,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        if (egress is None) == (resolve is None):
            raise ValueError("pass exactly one of egress and resolve")
        if timeout <= 0 or passage_max_chars < 1:
            raise ValueError("timeout and passage_max_chars must be positive")
        self._egress = egress
        self._resolve = resolve
        self._timeout = timeout
        self._max_chars = passage_max_chars
        self._now = now

    def _getter(self) -> HttpGetter:
        if self._egress is not None:
            return self._egress
        resolve = self._resolve
        if resolve is None:  # pragma: no cover - the constructor sets one of the two
            raise FetchRefused("unavailable")
        try:
            return resolve()
        except Exception:
            raise FetchRefused("unavailable") from None

    async def fetch(
        self, url: str, *, allowed_urls: Collection[str], query: str | None = None
    ) -> FetchedPage:
        check_fetch_url(url, allowed_urls)
        started = time.perf_counter()
        try:
            page = await self._fetch(url)
        except FetchRefused as exc:
            _logger.warning(
                "research_fetch_failed",
                extra={
                    "fetch_via": "egress",
                    "fetch_reason": exc.reason,
                    "duration_ms": _elapsed_ms(started),
                },
            )
            raise
        _logger.info(
            "research_fetch",
            extra={"fetch_via": "egress", "duration_ms": _elapsed_ms(started)},
        )
        return page

    async def _fetch(self, url: str) -> FetchedPage:
        getter = self._getter()
        try:
            async with asyncio.timeout(self._timeout):
                response = await getter.get(url, headers={"Accept": _ACCEPT})
        except TimeoutError:
            raise FetchRefused("timeout") from None
        except EgressBlockedError:
            raise FetchRefused("blocked") from None
        except EgressTooLargeError:
            raise FetchRefused("too_large") from None
        except EgressTimeoutError:
            raise FetchRefused("timeout") from None
        except EgressError:
            raise FetchRefused("http_error") from None
        if response.status != 200:
            raise FetchRefused("http_error")
        media_type = response.media_type
        title: str | None = None
        try:
            if media_type in _HTML_TYPES or (
                media_type is None and _looks_like_html(response.content)
            ):
                title, text = await asyncio.to_thread(
                    html_to_text,
                    response.content,
                    charset=response.charset,
                    max_chars=self._max_chars,
                )
            elif media_type == "text/plain":
                text = await asyncio.to_thread(
                    plain_text,
                    response.content,
                    charset=response.charset,
                    max_chars=self._max_chars,
                )
            else:
                raise FetchRefused("unsupported_type")
        except FetchRefused:
            raise
        except Exception:
            raise FetchRefused("unsupported_type") from None
        if not text:
            raise FetchRefused("unsupported_type")
        return FetchedPage(url=url, title=title, text=text, via="egress", fetched_at=self._now())


type WebIqState = Literal["off", "on", "degraded"]


class FallbackFetcher(ResearchFetcher):
    """Web IQ first, egress second (``FetchedPage.via`` records which served).

    Web IQ refusing a page by policy (``blocked``) is final: the page is not fetched around it.
    ``failures_to_open`` consecutive Web IQ outages (unavailable, timeout) open a circuit for
    ``cooldown_seconds``: fetches go straight to egress, then one probe decides.
    """

    def __init__(
        self,
        webiq: WebIqFetcher | None,
        egress: EgressFetcher,
        *,
        failures_to_open: int = 3,
        cooldown_seconds: float = 300.0,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if failures_to_open < 1 or cooldown_seconds < 0:
            raise ValueError("failures_to_open must be positive and cooldown not negative")
        self._webiq = webiq
        self._egress = egress
        self._failures_to_open = failures_to_open
        self._cooldown = cooldown_seconds
        self._monotonic = monotonic
        self._failures = 0
        self._open_until: float | None = None

    @property
    def webiq_enabled(self) -> bool:
        """Web IQ is configured (fetches try it first)."""
        return self._webiq is not None

    @property
    def state(self) -> WebIqState:
        """``off`` (not configured), ``on`` or ``degraded`` (the circuit is open)."""
        if self._webiq is None:
            return "off"
        if self._open_until is not None and self._monotonic() < self._open_until:
            return "degraded"
        return "on"

    def _circuit_open(self) -> bool:
        if self._open_until is None:
            return False
        if self._monotonic() < self._open_until:
            return True
        self._open_until = None  # half-open: one probe, and one more outage re-opens it
        self._failures = self._failures_to_open - 1
        return False

    def _outage(self) -> None:
        self._failures += 1
        if self._failures >= self._failures_to_open and self._open_until is None:
            self._open_until = self._monotonic() + self._cooldown
            _logger.warning("research_webiq_circuit_open", extra={"count": self._failures})

    async def fetch(
        self, url: str, *, allowed_urls: Collection[str], query: str | None = None
    ) -> FetchedPage:
        check_fetch_url(url, allowed_urls)
        if self._webiq is not None and not self._circuit_open():
            try:
                page = await self._webiq.fetch(url, allowed_urls=allowed_urls, query=query)
            except FetchRefused as exc:
                if exc.reason in _TERMINAL_REASONS:
                    self._failures = 0
                    raise
                if exc.reason in _OUTAGE_REASONS:
                    self._outage()
                else:
                    self._failures = 0
                _logger.info(
                    "research_webiq_fallback",
                    extra={"fetch_via": "egress", "fetch_reason": exc.reason},
                )
            else:
                self._failures = 0
                return page
        return await self._egress.fetch(url, allowed_urls=allowed_urls, query=query)

    async def aclose(self) -> None:
        if self._webiq is not None:
            await self._webiq.aclose()
