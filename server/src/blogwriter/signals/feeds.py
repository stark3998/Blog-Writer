"""Feed parsing and feed discovery: pure, synchronous, CPU-bound (run them in a thread).

Rewritten from the v1 crawler's ``parse_rss_feed``, ``discover_feed``, ``_article_id`` and
``_filter_by_age`` without its bugs:

- **Nothing here fetches.** v1 handed a URL to ``feedparser.parse``, which fetched it outside any
  egress policy. Here the caller downloads through the egress helper and passes the bytes, and
  they reach feedparser as a stream: feedparser treats a ``bytes`` argument as a *file name*
  first, so a feed whose body is ``/etc/passwd`` would make it read a local file.
- Item links are resolved against the final feed URL and must be absolute ``https`` URLs we are
  willing to store (``domain.base.check_https_url``); anything else is skipped, so an ``http``
  item can never become an idea.
- Dedupe keys are ``domain.hashing.url_sha256`` (normalized URLs). v1 lowercased whole URLs,
  which merged distinct paths.
- Dated items are sorted newest first before the cap; undated items keep feed order after them.
  Age filtering (:func:`within_age`) keeps undated items, like v1, and treats a date more than a
  day in the future as unknown.
- Discovery reads ``<link rel="alternate">`` tags (RSS, Atom, RDF and JSON Feed types) and never
  probes guessed paths on the owner's site.

JSON Feed (``https://jsonfeed.org/version/1.x``) is parsed without feedparser, which does not
support it. Titles, publisher names and feed titles are untrusted: they come back as one line of
plain text (:func:`blogwriter.signals._text.clean_text`).
"""

from __future__ import annotations

import calendar
import io
import json
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import IO, Final, Protocol, cast
from urllib.parse import urljoin, urlsplit

import feedparser  # pyright: ignore[reportMissingTypeStubs]
from bs4 import BeautifulSoup, Tag
from bs4.filter import SoupStrainer

from blogwriter.domain.base import check_https_url, parse_iso, to_iso
from blogwriter.domain.hashing import normalize_url
from blogwriter.signals._text import clean_text

__all__ = [
    "FEED_LINK_TYPES",
    "MAX_PUBLISHER_CHARS",
    "MAX_TITLE_CHARS",
    "DiscoveredFeed",
    "FeedItem",
    "FeedParseError",
    "ParsedFeed",
    "discover_feed_links",
    "discover_feeds",
    "looks_like_feed",
    "parse_feed",
    "parse_feed_document",
    "within_age",
]

MAX_TITLE_CHARS: Final = 300
"""``Idea.title``'s limit."""
MAX_PUBLISHER_CHARS: Final = 200
MAX_DISCOVERED_FEEDS: Final = 20
FUTURE_SKEW: Final = timedelta(days=1)
"""A publication date further ahead than this is treated as unknown (a broken feed clock)."""

FEED_LINK_TYPES: Final = frozenset(
    {
        "application/rss+xml",
        "application/atom+xml",
        "application/rdf+xml",
        "application/feed+json",
        "application/xml",
        "text/xml",
    }
)
"""``<link rel="alternate" type=...>`` values that name a feed. ``application/json`` is left out on
purpose: WordPress and oEmbed use it for API documents, not feeds."""

_JSON_FEED_PREFIX: Final = "https://jsonfeed.org/version/"
_BOM: Final = b"\xef\xbb\xbf"


@dataclass(frozen=True, slots=True)
class FeedItem:
    """One feed entry: an absolute ``https`` link, a plain-text title, the publication time
    (:func:`blogwriter.domain.base.to_iso`) and the publisher (the feed's or the entry's
    source title). Never a summary or content."""

    url: str
    title: str
    published_at: str | None
    publisher: str | None


@dataclass(frozen=True, slots=True)
class ParsedFeed:
    """A parsed feed: its title, its format (feedparser's version code, or ``jsonfeed``) and its
    items, newest first."""

    title: str | None
    format: str
    items: tuple[FeedItem, ...]


class FeedParseError(ValueError):
    """The bytes are not a feed (``reason``: ``not_a_feed``)."""

    def __init__(self, reason: str = "not_a_feed") -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class DiscoveredFeed:
    """A feed a page links to: absolute ``https`` URL, the link's ``title`` and ``type``."""

    url: str
    title: str | None
    type: str


# --------------------------------------------------------------------------------------------
# Typed boundary around feedparser (it ships no type information)
# --------------------------------------------------------------------------------------------


class _FeedParse(Protocol):
    def __call__(
        self,
        url_file_stream_or_string: IO[bytes],
        *,
        response_headers: Mapping[str, str],
        resolve_relative_uris: bool,
        sanitize_html: bool,
    ) -> Mapping[str, object]: ...


def _feedparser_parse() -> _FeedParse:
    # feedparser.parse matches _FeedParse for a stream argument; it returns a dict subclass.
    return feedparser.parse  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]


def _mapping(value: object) -> Mapping[str, object]:
    # A plain copy: feedparser's dict subclass remaps some keys on lookup (with warnings).
    if isinstance(value, dict):
        return dict(cast(dict[str, object], value))
    return cast(Mapping[str, object], value) if isinstance(value, Mapping) else {}


def _sequence(value: object) -> Sequence[object]:
    if isinstance(value, list | tuple):
        return cast(Sequence[object], value)
    return ()


def _text(value: object) -> str | None:
    return value if isinstance(value, str) else None


# --------------------------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------------------------


def _check_base(base_url: str) -> str:
    parts = urlsplit(base_url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("base_url must be an absolute http(s) URL")
    return base_url


def _host(url: str) -> str | None:
    host = urlsplit(url).hostname
    if not host:
        return None
    return host.removeprefix("www.")


def _https_link(raw: object, base_url: str) -> str | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        absolute = urljoin(base_url, raw.strip())
        return check_https_url(absolute)
    except ValueError:
        return None


def _struct_time_iso(value: object) -> str | None:
    if not isinstance(value, time.struct_time):
        return None
    try:
        seconds = calendar.timegm(value)
        return to_iso(datetime.fromtimestamp(seconds, tz=UTC))
    except (OverflowError, OSError, TypeError, ValueError):
        return None


def _rfc3339_iso(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        moment = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    try:
        return to_iso(moment)
    except (OverflowError, ValueError):
        return None


def _entry_link(entry: Mapping[str, object], base_url: str) -> str | None:
    link = _https_link(entry.get("link"), base_url)
    if link is not None:
        return link
    for raw in _sequence(entry.get("links")):
        candidate = _mapping(raw)
        if _text(candidate.get("rel")) in (None, "alternate"):
            link = _https_link(candidate.get("href"), base_url)
            if link is not None:
                return link
    return None


def _finish(items: list[FeedItem], *, title: str | None, format: str, max_items: int) -> ParsedFeed:
    seen: set[str] = set()
    unique: list[FeedItem] = []
    for item in items:
        try:
            key = normalize_url(item.url)
        except ValueError:
            continue
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    dated = sorted(
        (item for item in unique if item.published_at is not None),
        key=lambda item: item.published_at or "",
        reverse=True,
    )
    undated = [item for item in unique if item.published_at is None]
    return ParsedFeed(title=title, format=format, items=tuple((dated + undated)[:max_items]))


def _parse_json_feed(
    document: Mapping[str, object], *, base_url: str, max_items: int
) -> ParsedFeed:
    feed_title = clean_text(document.get("title"), max_chars=MAX_PUBLISHER_CHARS)
    publisher = feed_title or _host(base_url)
    items: list[FeedItem] = []
    for raw in _sequence(document.get("items")):
        entry = _mapping(raw)
        url = _https_link(entry.get("url"), base_url) or _https_link(
            entry.get("external_url"), base_url
        )
        title = clean_text(entry.get("title"), max_chars=MAX_TITLE_CHARS)
        if url is None or title is None:
            continue
        published = _rfc3339_iso(entry.get("date_published")) or _rfc3339_iso(
            entry.get("date_modified")
        )
        items.append(FeedItem(url=url, title=title, published_at=published, publisher=publisher))
    return _finish(items, title=feed_title, format="jsonfeed", max_items=max_items)


def _json_feed(content: bytes) -> Mapping[str, object] | None:
    stripped = content.removeprefix(_BOM).lstrip()
    if not stripped.startswith(b"{"):
        return None
    try:
        document: object = json.loads(stripped.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        return None
    body = _mapping(document)
    version = _text(body.get("version"))
    if version is None or not version.startswith(_JSON_FEED_PREFIX):
        return None
    return body


def parse_feed_document(
    content: bytes, *, base_url: str, max_items: int, content_type: str | None = None
) -> ParsedFeed:
    """Parse RSS 0.9x/1.0/2.0, Atom or JSON Feed bytes downloaded from ``base_url``.

    ``base_url`` is the final URL of the download (relative links resolve against it).
    ``content_type`` is the response's ``Content-Type`` (it helps feedparser pick the
    encoding). Malformed XML is parsed leniently: entries before the damage are kept. Raises
    :class:`FeedParseError` when the bytes are not a feed at all.
    """
    _check_base(base_url)
    if max_items < 1:
        raise ValueError("max_items must be at least 1")
    json_feed = _json_feed(content)
    if json_feed is not None:
        return _parse_json_feed(json_feed, base_url=base_url, max_items=max_items)
    headers = {"content-location": base_url}
    if content_type:
        headers["content-type"] = content_type
    parsed = _mapping(
        _feedparser_parse()(
            io.BytesIO(content),
            response_headers=headers,
            resolve_relative_uris=False,
            sanitize_html=False,
        )
    )
    version = _text(parsed.get("version")) or ""
    entries = _sequence(parsed.get("entries"))
    if not version and not entries:
        raise FeedParseError()
    feed = _mapping(parsed.get("feed"))
    feed_title = clean_text(feed.get("title"), max_chars=MAX_PUBLISHER_CHARS)
    publisher = feed_title or _host(base_url)
    items: list[FeedItem] = []
    for raw in entries:
        entry = _mapping(raw)
        url = _entry_link(entry, base_url)
        title = clean_text(entry.get("title"), max_chars=MAX_TITLE_CHARS)
        if url is None or title is None:
            continue
        published = _struct_time_iso(entry.get("published_parsed")) or _struct_time_iso(
            entry.get("updated_parsed")
        )
        source = _mapping(entry.get("source"))
        item_publisher = clean_text(source.get("title"), max_chars=MAX_PUBLISHER_CHARS)
        items.append(
            FeedItem(
                url=url,
                title=title,
                published_at=published,
                publisher=item_publisher or publisher,
            )
        )
    return _finish(items, title=feed_title, format=version or "unknown", max_items=max_items)


def parse_feed(
    content: bytes, *, base_url: str, max_items: int, content_type: str | None = None
) -> list[FeedItem]:
    """The items of a feed (:func:`parse_feed_document`), newest first, at most ``max_items``.

    Synchronous and CPU-bound: call it with ``asyncio.to_thread``.
    """
    document = parse_feed_document(
        content, base_url=base_url, max_items=max_items, content_type=content_type
    )
    return list(document.items)


def within_age(item: FeedItem, *, now: datetime, max_age_days: int) -> FeedItem | None:
    """``item`` if it is recent enough to triage, else ``None`` (v1 ``_filter_by_age``).

    Undated items are kept (they are deduplicated by URL anyway). A date more than
    :data:`FUTURE_SKEW` ahead of ``now`` is dropped from the item (it is unknown, not new).
    """
    if item.published_at is None:
        return item
    published = parse_iso(item.published_at)
    if published > now + FUTURE_SKEW:
        return FeedItem(url=item.url, title=item.title, published_at=None, publisher=item.publisher)
    if published < now - timedelta(days=max_age_days):
        return None
    return item


# --------------------------------------------------------------------------------------------
# Discovery
# --------------------------------------------------------------------------------------------

_XML_START: Final = (b"<?xml", b"<rss", b"<feed", b"<rdf:rdf")


def _head(content: bytes) -> bytes:
    return content.removeprefix(_BOM).lstrip()[:1024].lower()


def looks_like_feed(content: bytes, media_type: str | None = None) -> bool:
    """Whether a response is probably a feed rather than an HTML page (cheap sniffing)."""
    if media_type is not None:
        kind = media_type.split(";", 1)[0].strip().lower()
        if kind in FEED_LINK_TYPES or kind == "application/json":
            return True
        if kind in ("text/html", "application/xhtml+xml"):
            return False
    head = _head(content)
    if head.startswith(b"{"):
        return True
    return head.startswith(_XML_START) and b"<html" not in head


def _decode_html(content: bytes) -> str:
    raw = content.removeprefix(_BOM)
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace")


def _attr(tag: Tag, name: str) -> str | None:
    value = tag.get(name)
    if isinstance(value, str):
        return value
    if value is None:
        return None
    return " ".join(value)


def discover_feed_links(html: bytes, *, base_url: str) -> list[DiscoveredFeed]:
    """Feeds an HTML page links to with ``<link rel="alternate" type="...">``, in page order.

    Relative ``href`` values resolve against ``<base href>`` (if any) and ``base_url`` (the
    page's final URL). Only absolute ``https`` URLs are returned (an ``http`` feed is refused at
    crawl time anyway), each once, at most 20. Synchronous: call it in a thread.
    """
    _check_base(base_url)
    head = _head(html)
    if b"<" not in html or (head.startswith(b"<?xml") and b"<html" not in head):
        return []  # not HTML (and BeautifulSoup would warn about it)
    soup = BeautifulSoup(
        _decode_html(html), "html.parser", parse_only=SoupStrainer(["link", "base"])
    )
    base = base_url
    found: list[DiscoveredFeed] = []
    seen: set[str] = set()
    for tag in soup.find_all(["base", "link"]):
        if tag.name == "base":
            href = _attr(tag, "href")
            if href and base == base_url:
                try:
                    base = _check_base(urljoin(base_url, href.strip()))
                except ValueError:
                    base = base_url
            continue
        rel = {token.lower() for token in (_attr(tag, "rel") or "").split()}
        kind = (_attr(tag, "type") or "").split(";", 1)[0].strip().lower()
        if "alternate" not in rel or kind not in FEED_LINK_TYPES:
            continue
        url = _https_link(_attr(tag, "href"), base)
        if url is None or url in seen:
            continue
        seen.add(url)
        title = clean_text(_attr(tag, "title"), max_chars=MAX_PUBLISHER_CHARS)
        found.append(DiscoveredFeed(url=url, title=title, type=kind))
        if len(found) >= MAX_DISCOVERED_FEEDS:
            break
    return found


def discover_feeds(html: bytes, *, base_url: str) -> list[str]:
    """The feed URLs an HTML page links to (:func:`discover_feed_links`). Synchronous."""
    return [feed.url for feed in discover_feed_links(html, base_url=base_url)]
