from __future__ import annotations

import pytest

from article_render.slug import Slugger, slugify


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
        ("x" * 100, "x" * 64),
        ("word " * 30, ("word-" * 13)[:-1]),
    ],
)
def test_slugify(text: str, expected: str) -> None:
    assert slugify(text) == expected


def test_duplicates_and_reserved_ids() -> None:
    slugger = Slugger()
    assert [slugger.slug(t) for t in ("Overview", "Overview", "Overview", "FN1", "fnref2")] == [
        "overview",
        "overview-1",
        "overview-2",
        "fn1-1",
        "fnref2-1",
    ]
