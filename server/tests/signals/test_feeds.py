"""Feed parsing (goldens: RSS 2.0, Atom, malformed, JSON Feed), age filtering and discovery."""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final

import pytest

from blogwriter.signals.feeds import (
    FeedItem,
    FeedParseError,
    discover_feed_links,
    discover_feeds,
    looks_like_feed,
    parse_feed,
    parse_feed_document,
    within_age,
)
from blogwriter.signals.testing import FeedEntry, rss_bytes

GOLDENS: Final = Path(__file__).parent / "goldens"
UPDATE_SNAPSHOTS: Final = os.environ.get("UPDATE_SNAPSHOTS") == "1"
NOW: Final = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)

CASES: Final = [
    ("rss20.xml", "https://news.example.org/feed.xml", "application/rss+xml; charset=utf-8"),
    ("atom10.xml", "https://blog.example.net/agents/feed.atom", "application/atom+xml"),
    ("malformed.xml", "https://bulletin.example.com/rss", "text/xml"),
    ("jsonfeed.json", "https://log.example.dev/feed.json", "application/feed+json"),
]


def check_golden(name: str, actual: str) -> None:
    path = GOLDENS / name
    if UPDATE_SNAPSHOTS:
        path.write_text(actual, encoding="utf-8")
    assert path.exists(), f"missing golden {path}; run UPDATE_SNAPSHOTS=1 pytest"
    expected = path.read_text(encoding="utf-8")
    assert actual == expected, f"{path.name} differs from its golden (UPDATE_SNAPSHOTS=1 to accept)"


@pytest.mark.parametrize(("name", "base_url", "content_type"), CASES)
def test_parse_goldens(name: str, base_url: str, content_type: str) -> None:
    content = (GOLDENS / name).read_bytes()
    document = parse_feed_document(
        content, base_url=base_url, max_items=50, content_type=content_type
    )
    actual = {
        "title": document.title,
        "format": document.format,
        "items": [asdict(item) for item in document.items],
    }
    check_golden(f"{name}.expected.json", json.dumps(actual, indent=2, ensure_ascii=False) + "\n")
    # Summaries and content are never read into an item.
    assert "SUMMARY-MARKER" not in repr(document)


def test_rss_rules() -> None:
    items = parse_feed(
        (GOLDENS / "rss20.xml").read_bytes(),
        base_url="https://news.example.org/feed.xml",
        max_items=50,
    )
    urls = [item.url for item in items]
    assert all(url.startswith("https://") for url in urls)  # the http item is skipped
    assert "https://news.example.org/posts/token-binding" in urls  # relative link resolved
    assert len([url for url in urls if "oauth-21" in url]) == 1  # same normalized URL once
    assert items[0].title == "Passkeys rollout lessons"  # newest first, whitespace collapsed
    assert items[-1].published_at is None  # undated last
    assert all(item.title for item in items)  # untitled entries are skipped
    assert (
        parse_feed(
            (GOLDENS / "rss20.xml").read_bytes(), base_url="https://news.example.org/f", max_items=2
        )
        == items[:2]
    )


def test_titles_become_one_line_of_plain_text() -> None:
    content = rss_bytes(
        [
            FeedEntry("A <b>bold</b> &amp;amp; claim\u202e reversed", "https://x.example.com/1"),
            FeedEntry("x" * 400, "https://x.example.com/2"),
        ],
        title="Feed\u200b name",
    )
    first, second = parse_feed(content, base_url="https://x.example.com/feed", max_items=10)
    assert first.title == "A bold & claim reversed"
    assert first.publisher == "Feed name"
    assert len(second.title) == 300
    assert second.title.endswith("\u2026")


def test_bytes_are_never_treated_as_a_file_name(tmp_path: Path) -> None:
    # feedparser opens a bytes argument as a path first: a feed body naming a local file must
    # not make it read that file.
    local = tmp_path / "local-feed.xml"
    local.write_bytes(rss_bytes([FeedEntry("Local secret item", "https://local.example.com/a")]))
    with pytest.raises(FeedParseError):
        parse_feed(str(local).encode(), base_url="https://evil.example.com/feed", max_items=10)


@pytest.mark.parametrize(
    "content",
    [
        b"<!doctype html><html><head><title>Not a feed</title></head><body>Hi</body></html>",
        b"",
        b'{"version": "1", "items": []}',
        b"plain text",
    ],
)
def test_not_a_feed(content: bytes) -> None:
    with pytest.raises(FeedParseError) as caught:
        parse_feed_document(content, base_url="https://example.com/", max_items=5)
    assert caught.value.reason == "not_a_feed"


def test_arguments_are_checked() -> None:
    content = rss_bytes([FeedEntry("A", "https://a.example.com/1")])
    with pytest.raises(ValueError, match="base_url"):
        parse_feed(content, base_url="/relative", max_items=5)
    with pytest.raises(ValueError, match="max_items"):
        parse_feed(content, base_url="https://a.example.com/", max_items=0)


def test_within_age_ports_the_v1_filter_without_its_bugs() -> None:
    def item(published: datetime | None) -> FeedItem:
        stamp = None if published is None else published.strftime("%Y-%m-%dT%H:%M:%S.000000Z")
        return FeedItem(
            url="https://a.example.com/1", title="A", published_at=stamp, publisher=None
        )

    recent = item(NOW - timedelta(days=13))
    assert within_age(recent, now=NOW, max_age_days=14) == recent
    assert within_age(item(NOW - timedelta(days=15)), now=NOW, max_age_days=14) is None
    undated = item(None)
    assert within_age(undated, now=NOW, max_age_days=14) == undated
    future = within_age(item(NOW + timedelta(days=3)), now=NOW, max_age_days=14)
    assert future is not None
    assert future.published_at is None  # a broken clock, not a fresh item
    near_future = item(NOW + timedelta(hours=2))
    assert within_age(near_future, now=NOW, max_age_days=14) == near_future


PAGE: Final = b"""<!doctype html>
<html><head>
<title>Site</title>
<base href="https://cdn.example.com/blog/">
<link rel="stylesheet" href="/style.css">
<link rel="alternate stylesheet" type="application/rss+xml" href="weird.xml" title="Odd">
<link rel="Alternate" type="application/rss+xml" title="Main &amp; feed" href="feed.xml">
<link rel="alternate" type="application/atom+xml" href="https://cdn.example.com/blog/feed.xml">
<link rel="alternate" type="application/json" href="/wp-json/wp/v2/pages/2">
<link rel="alternate" type="application/feed+json" href="https://other.example.org/feed.json">
<link rel="alternate" type="application/rss+xml" href="http://insecure.example.org/rss">
<link rel="alternate" type="application/rss+xml" href="javascript:alert(1)">
</head><body><link rel="alternate" type="text/xml" href="/comments.xml"></body></html>"""


def test_discovery_reads_alternate_links() -> None:
    links = discover_feed_links(PAGE, base_url="https://www.example.com/")
    assert [(link.url, link.title, link.type) for link in links] == [
        ("https://cdn.example.com/blog/weird.xml", "Odd", "application/rss+xml"),
        ("https://cdn.example.com/blog/feed.xml", "Main & feed", "application/rss+xml"),
        ("https://other.example.org/feed.json", None, "application/feed+json"),
        ("https://cdn.example.com/comments.xml", None, "text/xml"),
    ]
    assert discover_feeds(PAGE, base_url="https://www.example.com/") == [link.url for link in links]


@pytest.mark.parametrize(
    "content",
    [
        b"https://example.com/feed",  # resembles a URL: no markup at all
        b'<?xml version="1.0"?><rss version="2.0"><channel></channel></rss>',
        b"",
    ],
)
def test_discovery_ignores_non_html(content: bytes) -> None:
    assert discover_feed_links(content, base_url="https://example.com/") == []


def test_feed_sniffing() -> None:
    assert looks_like_feed(b"<html></html>", "application/rss+xml; charset=utf-8")
    assert not looks_like_feed(b"<?xml version='1.0'?><rss/>", "text/html")
    assert looks_like_feed(b"\xef\xbb\xbf  <?xml version='1.0'?><feed/>", None)
    assert looks_like_feed(b'{"version": "https://jsonfeed.org/version/1.1"}', None)
    assert not looks_like_feed(b"<?xml version='1.0'?><html><body/></html>", None)
    assert not looks_like_feed(b"<!doctype html><html></html>", "text/plain")
