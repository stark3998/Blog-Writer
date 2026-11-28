"""Heading ids: ASCII kebab-case, de-duplicated, never a footnote id."""

from __future__ import annotations

import pytest

from blogwriter.publish.dialect import SLUG_RE
from blogwriter.publish.slug import MAX_ID_LENGTH, Slugger, slugify


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Overview", "overview"),
        ("What changed in 2025?", "what-changed-in-2025"),
        ("C# and F# tooling", "c-and-f-tooling"),
        ("Ünïcödé — headings: résumé ☕", "unicode-headings-resume"),
        ("日本語のテキスト", "section"),
        ('"><script>alert(1)</script>', "script-alert-1-script"),
        ("  --Edge--  ", "edge"),
        ("", "section"),
        ("x" * 100, "x" * MAX_ID_LENGTH),
        ("word " * 30, ("word-" * 13)[:-1]),
    ],
)
def test_slugify(text: str, expected: str) -> None:
    assert slugify(text) == expected
    assert SLUG_RE.fullmatch(slugify(text))


def test_duplicates_and_reserved_ids() -> None:
    slugger = Slugger()
    assert [slugger.slug(t) for t in ("Overview", "Overview", "Overview", "FN1", "fnref2")] == [
        "overview",
        "overview-1",
        "overview-2",
        "fn1-1",
        "fnref2-1",
    ]
