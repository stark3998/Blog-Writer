"""validate_published_doc: good documents pass; every schema rule and invariant has a bad sample."""

from __future__ import annotations

import math
import random
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final, cast

import pytest

from blogwriter.publish.contract import (
    FUTURE_TOLERANCE,
    MAX_DOCUMENT_BYTES,
    MAX_MARKDOWN_BYTES,
    ContractError,
    PublishedContract,
    validate_published_doc,
)

Doc = dict[str, Any]
DocFactory = Callable[[], Doc]
Path_ = tuple[str | int, ...]

DELETE: Final = object()
APPEND: Final = -1
SLUG: Final = "least-privilege-tool-calls-for-ai-agents"
MEDIA: Final = f"/blog-media/{SLUG}"


def _apply(doc: Doc, changes: Mapping[Path_, object]) -> Doc:
    """Set (or delete with DELETE, or append with index APPEND) values at nested paths."""
    for path, value in changes.items():
        target: Any = doc
        for key in path[:-1]:
            target = target[key]
        last = path[-1]
        if value is DELETE:
            del target[last]
        elif last == APPEND:
            target.append(value)
        else:
            target[last] = value
    return doc


def _codes(errors: list[ContractError]) -> set[tuple[str, str]]:
    return {(error.code, error.path) for error in errors}


# --- Good documents -------------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["published", "minimal", "redirect", "unpublished"])
def test_good_samples_pass(
    name: str, samples: dict[str, DocFactory], contract: PublishedContract, now: datetime
) -> None:
    assert contract.validate(samples[name](), now=now) == []


def test_document_read_back_from_cosmos_passes(
    published_doc: DocFactory, contract: PublishedContract, now: datetime
) -> None:
    doc = published_doc() | {
        "_rid": "x1sAAJ7mPQ8BAAAAAAAAAA==",
        "_self": "dbs/x1sAAA==/colls/x1sAAJ7mPQ8=/docs/x1sAAJ7mPQ8BAAAAAAAAAA==/",
        "_etag": '"0000d00e-0000-0100-0000-6745a1b20000"',
        "_attachments": "attachments/",
        "_ts": 1764150000,
    }
    assert contract.validate(doc, now=now) == []


def test_redirect_stub_read_back_from_cosmos_passes(
    redirect_doc: DocFactory, contract: PublishedContract, now: datetime
) -> None:
    doc = redirect_doc() | {"_etag": '"1"', "_ts": 1764150000}
    assert contract.validate(doc, now=now) == []


def test_publishing_at_now_and_within_the_clock_skew_allowance_passes(
    minimal_doc: DocFactory, contract: PublishedContract, now: datetime
) -> None:
    for offset in (timedelta(0), FUTURE_TOLERANCE):
        stamp = (now + offset).strftime("%Y-%m-%dT%H:%M:%SZ")
        doc = _apply(minimal_doc(), {("publishedAt",): stamp, ("updatedAt",): stamp})
        assert contract.validate(doc, now=now) == [], offset


def test_future_tolerance_is_small() -> None:
    assert timedelta(0) < FUTURE_TOLERANCE <= timedelta(minutes=5)


def test_module_function_uses_the_current_time(minimal_doc: DocFactory) -> None:
    past = _apply(
        minimal_doc(),
        {("publishedAt",): "2020-01-01T00:00:00Z", ("updatedAt",): "2020-01-02T00:00:00Z"},
    )
    assert validate_published_doc(past) == []
    future = datetime.now(UTC) + timedelta(days=2)
    stamp = future.strftime("%Y-%m-%dT%H:%M:%SZ")
    doc = _apply(minimal_doc(), {("publishedAt",): stamp, ("updatedAt",): stamp})
    assert _codes(validate_published_doc(doc)) == {
        ("published-in-future", "/publishedAt"),
        ("updated-in-future", "/updatedAt"),
    }


def test_now_must_be_timezone_aware(minimal_doc: DocFactory, contract: PublishedContract) -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        contract.validate(minimal_doc(), now=datetime(2026, 11, 26, 12, 0, 0))


def test_now_may_use_any_timezone(minimal_doc: DocFactory, contract: PublishedContract) -> None:
    eastern = datetime(2026, 11, 26, 7, 0, 0, tzinfo=UTC).astimezone()
    doc = _apply(
        minimal_doc(),
        {("publishedAt",): "2026-11-26T07:00:00Z", ("updatedAt",): "2026-11-26T07:00:00Z"},
    )
    assert contract.validate(doc, now=eastern) == []


def test_error_string_names_path_and_code() -> None:
    assert str(ContractError("id-slug-mismatch", "/id", "must equal slug")) == (
        "/id: must equal slug [id-slug-mismatch]"
    )
    assert str(ContractError("not-json", "", "bad")) == "/: bad [not-json]"


# --- Bad documents --------------------------------------------------------------------------------


@dataclass(frozen=True)
class Case:
    name: str
    changes: Mapping[Path_, object]
    expected: frozenset[tuple[str, str]]
    base: str = "published"


def case(
    name: str, changes: Mapping[Path_, object], *expected: tuple[str, str], base: str = "published"
) -> Case:
    return Case(name, changes, frozenset(expected), base)


CASES: Final = [
    # identity and versioning
    case("id differs from slug", {("id",): "another-post"}, ("id-slug-mismatch", "/id")),
    case(
        "slug not kebab-case",
        {("id",): "Bad Slug", ("slug",): "Bad Slug"},
        ("schema-pattern", "/id"),
        ("schema-pattern", "/slug"),
    ),
    case(
        "slug with a trailing newline (Python's $ would accept it)",
        {("id",): "my-post\n", ("slug",): "my-post\n"},
        ("schema-pattern", "/id"),
        ("schema-pattern", "/slug"),
        base="unpublished",
    ),
    case(
        "slug longer than 80",
        {("id",): "a" * 81, ("slug",): "a" * 81},
        ("schema-max-length", "/id"),
        ("schema-max-length", "/slug"),
        base="unpublished",
    ),
    case("schemaVersion 1", {("schemaVersion",): 1}, ("schema-const", "/schemaVersion")),
    case("schemaVersion missing", {("schemaVersion",): DELETE}, ("schema-required", "")),
    case("status unknown", {("status",): "draft"}, ("schema-enum", "/status")),
    case("status missing", {("status",): DELETE}, ("schema-required", ""), base="unpublished"),
    case("unknown property", {("author",): "me"}, ("schema-additional-properties", "")),
    case(
        "unknown Cosmos-looking property",
        {("_owner",): "x"},
        ("schema-additional-properties", ""),
    ),
    # plain text
    case("title with <", {("title",): "Why <script> fails"}, ("schema-pattern", "/title")),
    case("title with leading space", {("title",): " Title"}, ("schema-pattern", "/title")),
    case("title with trailing newline", {("title",): "Title\n"}, ("schema-pattern", "/title")),
    case("title with a line break", {("title",): "Two\nlines"}, ("schema-pattern", "/title")),
    case(
        "title with a line separator",
        {("title",): "Two\u2028lines"},
        ("schema-pattern", "/title"),
    ),
    case("title with a control character", {("title",): "Bell\x07"}, ("schema-pattern", "/title")),
    case("title with a BOM", {("title",): "\ufeffTitle"}, ("schema-pattern", "/title")),
    case("title too long", {("title",): "t" * 151}, ("schema-max-length", "/title")),
    case("title missing", {("title",): DELETE}, ("schema-required", "")),
    case("excerpt empty", {("excerpt",): ""}, ("schema-pattern", "/excerpt")),
    case("excerpt too long", {("excerpt",): "e" * 401}, ("schema-max-length", "/excerpt")),
    case(
        "seo title with <", {("seo", "metaTitle"): "<b>x</b>"}, ("schema-pattern", "/seo/metaTitle")
    ),
    case("seo empty", {("seo",): {}}, ("schema-min-properties", "/seo")),
    case("hero alt with <", {("heroImage", "alt"): "a<b"}, ("schema-pattern", "/heroImage/alt")),
    case(
        "series title with <",
        {("series", "title"): "<i>x</i>"},
        ("schema-pattern", "/series/title"),
    ),
    # tags
    case("tag with <", {("tags", 0): "<x>"}, ("schema-pattern", "/tags/0")),
    case("tag not a slug", {("tags", 0): "Zero Trust"}, ("schema-pattern", "/tags/0")),
    case("tag too long", {("tags", 0): "t" * 41}, ("schema-max-length", "/tags/0")),
    case("duplicate tags", {("tags", 1): "agents"}, ("schema-unique-items", "/tags")),
    case(
        "eleven tags",
        {("tags",): [f"tag-{n}" for n in range(11)]},
        ("schema-max-items", "/tags"),
    ),
    case("tags missing", {("tags",): DELETE}, ("schema-required", "")),
    # timestamps and dates
    case(
        "publishedAt with an offset instead of Z",
        {("publishedAt",): "2026-11-20T14:30:00+00:00"},
        ("schema-pattern", "/publishedAt"),
    ),
    case(
        "publishedAt with fractional seconds",
        {("publishedAt",): "2026-11-20T14:30:00.000Z"},
        ("schema-pattern", "/publishedAt"),
    ),
    case(
        "publishedAt as a date only",
        {("publishedAt",): "2026-11-20"},
        ("schema-pattern", "/publishedAt"),
    ),
    case(
        "publishedAt not a real day",
        {("publishedAt",): "2026-02-30T14:30:00Z", ("date",): DELETE},
        ("invalid-timestamp", "/publishedAt"),
    ),
    case(
        "publishedAt in year zero",
        {("publishedAt",): "0000-01-01T00:00:00Z", ("date",): DELETE},
        ("invalid-timestamp", "/publishedAt"),
    ),
    case(
        "publishedAt in the future",
        {("publishedAt",): "2026-11-26T12:01:01Z", ("updatedAt",): "2026-11-26T12:01:01Z"},
        ("published-in-future", "/publishedAt"),
        ("updated-in-future", "/updatedAt"),
        ("date-mismatch", "/date"),
        ("correction-date-out-of-range", "/corrections/0/date"),
    ),
    case(
        "updatedAt in the future",
        {("updatedAt",): "2026-11-27T12:00:00Z"},
        ("updated-in-future", "/updatedAt"),
        base="unpublished",
    ),
    case(
        "updatedAt before publishedAt",
        {("updatedAt",): "2026-11-20T14:29:59Z", ("corrections",): []},
        ("updated-before-published", "/updatedAt"),
    ),
    case(
        "updatedAt missing",
        {("updatedAt",): DELETE},
        ("schema-required", ""),
        base="redirect",
    ),
    case(
        "date is not the UTC date of publishedAt",
        {("date",): "2026-11-21"},
        ("date-mismatch", "/date"),
    ),
    case("date not a real day", {("date",): "2026-02-30"}, ("schema-format", "/date")),
    case(
        "date with a time",
        {("date",): "2026-11-20T00:00:00Z"},
        ("schema-pattern", "/date"),
        ("schema-format", "/date"),
    ),
    # URLs
    case(
        "sourceUrl over http",
        {("sourceUrl",): "http://example.com"},
        ("schema-pattern", "/sourceUrl"),
    ),
    case(
        "sourceUrl javascript",
        {("sourceUrl",): "javascript:alert(1)"},
        ("schema-pattern", "/sourceUrl"),
    ),
    case("sourceUrl empty", {("sourceUrl",): ""}, ("schema-pattern", "/sourceUrl")),
    case(
        "sourceUrl with a control character",
        {("sourceUrl",): "https://example.com/\x01"},
        ("schema-pattern", "/sourceUrl"),
    ),
    case(
        "canonicalUrl site-relative",
        {("canonicalUrl",): f"/blog/{SLUG}"},
        ("schema-pattern", "/canonicalUrl"),
    ),
    case(
        "citation url protocol-relative",
        {("citations", 0, "url"): "//evil.example/x"},
        ("schema-pattern", "/citations/0/url"),
    ),
    case(
        "citation url with a backslash trick",
        {("citations", 0, "url"): "/\\evil.example"},
        ("schema-pattern", "/citations/0/url"),
    ),
    case(
        "citation url data",
        {("citations", 0, "url"): "data:text/html,x"},
        ("schema-pattern", "/citations/0/url"),
    ),
    # images and assets
    case(
        "hero image from a data URL",
        {("heroImage", "url"): "data:image/png;base64,AAAA"},
        ("schema-pattern", "/heroImage/url"),
    ),
    case(
        "hero image not in assets",
        {("heroImage", "url"): f"{MEDIA}/missing.png"},
        ("image-not-in-assets", "/heroImage/url"),
    ),
    case(
        "og image not in assets",
        {
            ("assets",): [],
            ("htmlContent",): "<p>x</p>",
            ("toc",): [],
            ("heroImage", "url"): "https://i.example/h.png",
        },
        ("image-not-in-assets", "/ogImage/url"),
    ),
    case(
        "og image as SVG",
        {("ogImage", "url"): f"{MEDIA}/og.svg"},
        ("schema-pattern", "/ogImage/url"),
        ("image-not-in-assets", "/ogImage/url"),
    ),
    case("og image wrong width", {("ogImage", "width"): 1201}, ("schema-const", "/ogImage/width")),
    case("og image missing", {("ogImage",): DELETE}, ("schema-required", "")),
    case(
        "hero image without alt", {("heroImage", "alt"): DELETE}, ("schema-required", "/heroImage")
    ),
    case(
        "hero image zero width", {("heroImage", "width"): 0}, ("schema-minimum", "/heroImage/width")
    ),
    case(
        "hero image huge height",
        {("heroImage", "height"): 16385},
        ("schema-maximum", "/heroImage/height"),
    ),
    case(
        "hero image extra field",
        {("heroImage", "caption"): "x"},
        ("schema-additional-properties", "/heroImage"),
    ),
    case(
        "asset outside the document's folder",
        {
            ("assets", 0, "path"): "/blog-media/other-post/hero-7d1e0b.png",
            ("heroImage", "url"): "/blog-media/other-post/hero-7d1e0b.png",
        },
        ("asset-location", "/assets/0/path"),
    ),
    case(
        "asset blob outside the document's folder",
        {("assets", 0, "blob"): "other-post/hero-7d1e0b.png"},
        ("asset-location", "/assets/0/blob"),
    ),
    case(
        "asset blob names another file",
        {("assets", 0, "blob"): f"{SLUG}/hero-000000.png"},
        ("asset-location", "/assets/0/blob"),
    ),
    case(
        "asset path with ..",
        {("assets", 0, "path"): f"{MEDIA}/../x.png"},
        ("schema-pattern", "/assets/0/path"),
        ("image-not-in-assets", "/heroImage/url"),
    ),
    case(
        "asset path upper case",
        {("assets", 0, "path"): f"{MEDIA}/Hero.png"},
        ("schema-pattern", "/assets/0/path"),
        ("image-not-in-assets", "/heroImage/url"),
    ),
    case(
        "asset that is not an image",
        {
            ("assets", APPEND): {
                "path": f"{MEDIA}/a.html",
                "blob": f"{SLUG}/a.html",
                "sha256": "0" * 64,
            }
        },
        ("schema-pattern", "/assets/3/path"),
        ("schema-pattern", "/assets/3/blob"),
    ),
    case(
        "asset sha256 upper case",
        {("assets", 0, "sha256"): "A" * 64},
        ("schema-pattern", "/assets/0/sha256"),
    ),
    case(
        "duplicate asset path",
        {
            ("assets", APPEND): {
                "path": f"{MEDIA}/og-7d1e0b.png",
                "blob": f"{SLUG}/og-7d1e0b.png",
                "sha256": "1" * 64,
            }
        },
        ("duplicate-asset-path", "/assets/3/path"),
    ),
    # series and numbers
    case(
        "series total before part",
        {("series", "total"): 1},
        ("series-total-before-part", "/series/total"),
    ),
    case("series part zero", {("series", "part"): 0}, ("schema-minimum", "/series/part")),
    case("series without title", {("series", "title"): DELETE}, ("schema-required", "/series")),
    case(
        "reading time zero", {("readingTimeMinutes",): 0}, ("schema-minimum", "/readingTimeMinutes")
    ),
    case("word count as text", {("wordCount",): "1318"}, ("schema-type", "/wordCount")),
    case("word count fractional", {("wordCount",): 13.5}, ("schema-type", "/wordCount")),
    case("word count boolean", {("wordCount",): True}, ("schema-type", "/wordCount")),
    case("pillar not a slug", {("pillar",): "AI Agents"}, ("schema-pattern", "/pillar")),
    case("format missing", {("format",): DELETE}, ("schema-required", "")),
    case("sourceType empty", {("sourceType",): ""}, ("schema-pattern", "/sourceType")),
    # toc
    case(
        "toc entry missing",
        {("toc",): DELETE},
        ("schema-required", ""),
    ),
    case(
        "toc drops a heading",
        {("toc", 9): DELETE},
        ("toc-mismatch", "/toc"),
    ),
    case(
        "toc with a heading htmlContent lacks",
        {("toc", APPEND): {"id": "appendix", "text": "Appendix", "level": 2}},
        ("toc-mismatch", "/toc/10"),
    ),
    case(
        "toc with the wrong level",
        {("toc", 2, "level"): 2},
        ("toc-mismatch", "/toc/2"),
    ),
    case("toc with level 4", {("toc", 2, "level"): 4}, ("schema-enum", "/toc/2/level")),
    case(
        "toc with a duplicate id",
        {("toc", 1, "id"): "threat-model"},
        ("duplicate-toc-id", "/toc/3/id"),
        ("toc-mismatch", "/toc/1"),
    ),
    case("toc text empty", {("toc", 0, "text"): ""}, ("schema-pattern", "/toc/0/text")),
    case("toc id not a slug", {("toc", 0, "id"): "Intro"}, ("schema-pattern", "/toc/0/id")),
    # citations and corrections
    case(
        "duplicate citation id",
        {("citations", 1, "id"): "owasp-llm-top-10"},
        ("duplicate-citation-id", "/citations/1/id"),
    ),
    case(
        "citation id too long",
        {("citations", 0, "id"): "c" * 65},
        ("schema-max-length", "/citations/0/id"),
    ),
    case(
        "citation without title",
        {("citations", 0, "title"): DELETE},
        ("schema-required", "/citations/0"),
    ),
    case(
        "citation month 13",
        {("citations", 0, "date"): "2024-13"},
        ("schema-pattern", "/citations/0/date"),
    ),
    case(
        "citation date not a real day",
        {("citations", 0, "date"): "2023-02-29"},
        ("invalid-date", "/citations/0/date"),
    ),
    case(
        "correction before publication",
        {("corrections", 0, "date"): "2026-11-19"},
        ("correction-date-out-of-range", "/corrections/0/date"),
    ),
    case(
        "correction after the last update",
        {("corrections", 0, "date"): "2026-11-25"},
        ("correction-date-out-of-range", "/corrections/0/date"),
    ),
    case(
        "corrections out of order",
        {("corrections", APPEND): {"date": "2026-11-21", "note": "Earlier fix."}},
        ("corrections-out-of-order", "/corrections/1/date"),
    ),
    case(
        "correction with an empty note",
        {("corrections", 0, "note"): ""},
        ("schema-pattern", "/corrections/0/note"),
    ),
    # status rules
    case(
        "published document with redirectTo",
        {("redirectTo",): "other-post"},
        ("schema-not", "/redirectTo"),
    ),
    case(
        "published document without htmlContent",
        {("htmlContent",): DELETE},
        ("schema-required", ""),
    ),
    case(
        "redirect without redirectTo",
        {("redirectTo",): DELETE},
        ("schema-required", ""),
        base="redirect",
    ),
    case(
        "redirect to itself",
        {("redirectTo",): "least-privilege-tool-calls"},
        ("redirect-to-self", "/redirectTo"),
        base="redirect",
    ),
    case(
        "redirect to an invalid slug",
        {("redirectTo",): "/blog/x"},
        ("schema-pattern", "/redirectTo"),
        base="redirect",
    ),
    case(
        "redirect stub with content",
        {("title",): "Old title", ("htmlContent",): "<p>x</p>"},
        ("schema-status-fields", "/title"),
        ("schema-status-fields", "/htmlContent"),
        base="redirect",
    ),
    case(
        "unpublished tombstone with content",
        {("htmlContent",): "<p>secret</p>", ("publishedAt",): "2026-11-20T14:30:00Z"},
        ("schema-status-fields", "/htmlContent"),
        ("schema-status-fields", "/publishedAt"),
        base="unpublished",
    ),
    case(
        "unpublished tombstone with redirectTo",
        {("redirectTo",): "other-post"},
        ("schema-status-fields", "/redirectTo"),
        base="unpublished",
    ),
    # htmlContent
    case(
        "htmlContent with a script",
        {("htmlContent",): "<p>Hello.</p><script>alert(1)</script>"},
        ("html-outside-profile", "/htmlContent"),
        base="minimal",
    ),
    case(
        "htmlContent as a v1 full document",
        {
            (
                "htmlContent",
            ): "<!DOCTYPE html><html><head><style>p{}</style></head><body><p>x</p></body></html>"
        },
        ("html-outside-profile", "/htmlContent"),
        base="minimal",
    ),
    case(
        "htmlContent cut off inside a tag (the page's next end tag would complete it)",
        {("htmlContent",): "<p>Hello.</p><img src=x onerror=alert(1)//"},
        ("html-outside-profile", "/htmlContent"),
        base="minimal",
    ),
    case(
        "htmlContent closing the page's article container",
        {("htmlContent",): "<p>Hello.</p></div><p>Outside.</p>"},
        ("html-outside-profile", "/htmlContent"),
        base="minimal",
    ),
    case(
        "htmlContent with an unclosed link",
        {("htmlContent",): '<p>Hello <a href="https://example.com">world.</p>'},
        ("html-outside-profile", "/htmlContent"),
        base="minimal",
    ),
    case(
        "htmlContent with duplicate ids",
        {
            ("htmlContent",): '<h2 id="a">A</h2><h3 id="a">B</h3>',
            ("toc",): [{"id": "a", "text": "A", "level": 2}, {"id": "a", "text": "B", "level": 3}],
        },
        ("duplicate-html-id", "/htmlContent"),
        ("duplicate-toc-id", "/toc/1/id"),
        base="minimal",
    ),
    case(
        "htmlContent media not in assets",
        {("htmlContent",): '<p><img src="/blog-media/minimal-post/x.png" alt="x"></p>'},
        ("media-not-in-assets", "/htmlContent"),
        base="minimal",
    ),
    case(
        "htmlContent with headings but an empty toc",
        {("htmlContent",): '<h2 id="intro">Intro</h2>'},
        ("toc-mismatch", "/toc"),
        base="minimal",
    ),
    case(
        "htmlContent with NUL",
        {("htmlContent",): "<p>a\x00b</p>"},
        ("schema-pattern", "/htmlContent"),
        base="minimal",
    ),
    case(
        "htmlContent empty",
        {("htmlContent",): ""},
        ("schema-min-length", "/htmlContent"),
        base="minimal",
    ),
    case(
        "mdxContent with a lone surrogate",
        {("mdxContent",): "x\ud800y"},
        ("schema-pattern", "/mdxContent"),
        ("invalid-unicode", ""),
        base="minimal",
    ),
    # JSON-ness
    case(
        "a non-JSON value",
        {("updatedAt",): datetime(2026, 11, 25, tzinfo=UTC)},
        ("schema-type", "/updatedAt"),
        ("not-json", ""),
        base="unpublished",
    ),
    case(
        "NaN",
        {("wordCount",): math.nan},
        ("schema-type", "/wordCount"),
        ("not-json", ""),
        base="minimal",
    ),
]


@pytest.mark.parametrize("bad", CASES, ids=[c.name for c in CASES])
def test_bad_documents_report_exactly_the_expected_errors(
    bad: Case, samples: dict[str, DocFactory], contract: PublishedContract, now: datetime
) -> None:
    doc = _apply(samples[bad.base](), bad.changes)
    errors = contract.validate(doc, now=now)
    assert _codes(errors) == bad.expected, [str(error) for error in errors]


def test_case_names_are_unique() -> None:
    names = [c.name for c in CASES]
    assert len(names) == len(set(names))


def test_every_invariant_code_has_a_bad_sample() -> None:
    covered = {code for c in CASES for code, _ in c.expected}
    invariants = {
        "id-slug-mismatch",
        "redirect-to-self",
        "invalid-timestamp",
        "published-in-future",
        "updated-in-future",
        "updated-before-published",
        "date-mismatch",
        "series-total-before-part",
        "duplicate-toc-id",
        "duplicate-citation-id",
        "invalid-date",
        "correction-date-out-of-range",
        "corrections-out-of-order",
        "duplicate-asset-path",
        "asset-location",
        "image-not-in-assets",
        "html-outside-profile",
        "duplicate-html-id",
        "media-not-in-assets",
        "toc-mismatch",
        "invalid-unicode",
        "not-json",
        "schema-status-fields",
    }
    # markdown-too-large and document-too-large have dedicated tests below.
    assert invariants <= covered


def test_markdown_over_the_server_cap(
    minimal_doc: DocFactory, contract: PublishedContract, now: datetime
) -> None:
    doc = _apply(minimal_doc(), {("mdxContent",): "a" * (MAX_MARKDOWN_BYTES + 1)})
    assert _codes(contract.validate(doc, now=now)) == {("markdown-too-large", "/mdxContent")}
    doc = _apply(minimal_doc(), {("mdxContent",): "\u00e9" * (MAX_MARKDOWN_BYTES // 2)})
    assert contract.validate(doc, now=now) == []  # exactly the cap, in two-byte characters


def test_document_over_the_cosmos_item_limit(
    minimal_doc: DocFactory, contract: PublishedContract, now: datetime
) -> None:
    doc = _apply(minimal_doc(), {("htmlContent",): "<p>" + "a" * MAX_DOCUMENT_BYTES + "</p>"})
    errors = contract.validate(doc, now=now)
    assert _codes(errors) == {("document-too-large", "")}
    assert len(errors[0].message) < 200


def test_html_findings_are_capped(
    minimal_doc: DocFactory, contract: PublishedContract, now: datetime
) -> None:
    doc = _apply(minimal_doc(), {("htmlContent",): "<script></script>" * 50})
    errors = contract.validate(doc, now=now)
    assert all(error.code == "html-outside-profile" for error in errors)
    assert len(errors) == 21
    assert errors[-1].message == "... and 30 more findings"


def test_messages_do_not_echo_long_content(
    minimal_doc: DocFactory, contract: PublishedContract, now: datetime
) -> None:
    secret = "confidential " * 5000
    doc = _apply(
        minimal_doc(),
        {("title",): secret, ("excerpt",): secret + "<", ("htmlContent",): f"<p>{secret}</p>\x00"},
    )
    errors = contract.validate(doc, now=now)
    assert {error.code for error in errors} >= {"schema-max-length", "schema-pattern"}
    assert all(len(error.message) < 300 for error in errors)
    assert all("confidential confidential" not in error.message for error in errors)


def test_messages_do_not_echo_long_property_names(
    unpublished_doc: DocFactory, contract: PublishedContract, now: datetime
) -> None:
    doc = unpublished_doc() | {"k" * 5000: 1, "confidential " * 400: 2}
    errors = contract.validate(doc, now=now)
    assert {error.code for error in errors} == {
        "schema-additional-properties",
        "schema-status-fields",
    }
    assert all(len(error.message) <= 200 for error in errors)


def test_schema_errors_come_in_a_stable_order(
    published_doc: DocFactory, contract: PublishedContract, now: datetime
) -> None:
    doc = _apply(
        published_doc(),
        {("toc", 9, "id"): "Bad", ("toc", 2, "id"): "Bad", ("title",): "<", ("tags", 0): "<"},
    )
    first = [str(e) for e in contract.validate(doc, now=now)]
    second = [str(e) for e in contract.validate(doc, now=now)]
    assert first == second
    schema_paths = [e.split(":")[0] for e in first if "[schema-" in e]
    assert schema_paths == ["/tags/0", "/title", "/toc/2/id", "/toc/9/id"]


def _children(node: object) -> list[tuple[str | int, object]]:
    if isinstance(node, dict):
        mapping = cast("dict[str, object]", node)
        return [(key, mapping[key]) for key in sorted(mapping)]
    if isinstance(node, list):
        return list(enumerate(cast("list[object]", node)))
    return []


JSON_SAMPLES: Final = [
    None,
    True,
    False,
    0,
    -1,
    1.5,
    "",
    "x",
    "<script>",
    "a" * 300,
    [],
    {},
    [1],
    {"a": 1},
]


@pytest.mark.parametrize("seed", range(20))
def test_random_corruption_never_raises(
    seed: int, samples: dict[str, DocFactory], contract: PublishedContract, now: datetime
) -> None:
    rng = random.Random(seed)
    for name in sorted(samples):
        doc = samples[name]()
        for _ in range(8):
            node: object = doc
            path: list[str | int] = []
            while (children := _children(node)) and rng.random() < 0.7:
                key, node = rng.choice(children)
                path.append(key)
            if path:
                _apply(doc, {tuple(path): rng.choice(JSON_SAMPLES)})
        errors = contract.validate(doc, now=now)
        assert all(isinstance(error, ContractError) for error in errors)


def test_deeply_nested_values_are_reported_not_raised(
    minimal_doc: DocFactory, contract: PublishedContract, now: datetime
) -> None:
    nested: Any = []
    for _ in range(5000):
        nested = [nested]
    doc = _apply(minimal_doc(), {("tags",): [nested, nested]})
    errors = contract.validate(doc, now=now)
    assert errors
    assert {error.code for error in errors} <= {
        "schema-type",
        "schema-unique-items",
        "schema-depth",
        "not-json",
    }
