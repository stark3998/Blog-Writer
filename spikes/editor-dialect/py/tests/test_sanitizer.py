"""Unit tests for the sanitizer profile's value rules, and the exported profile."""

from __future__ import annotations

import json

import pytest
from pygments.token import STANDARD_TYPES

from article_render.sanitizer import (
    PYGMENTS_CLASS_RE,
    attribute_filter,
    sanitize_html,
    sanitizer_profile,
)

from .conftest import SPIKE_DIR, assert_golden


@pytest.mark.parametrize(
    ("tag", "attribute", "value", "expected"),
    [
        ("a", "href", "https://ok.example/a?b=c", "https://ok.example/a?b=c"),
        ("a", "href", "mailto:a@b.example", "mailto:a@b.example"),
        ("a", "href", "#fnref1:2", "#fnref1:2"),
        ("a", "href", "/blog/post", "/blog/post"),
        ("a", "href", "//evil.example", None),
        ("a", "href", "/\\evil.example", None),
        ("a", "href", "http://insecure.example", None),
        ("a", "href", "blog/relative", None),
        ("a", "href", "https://ok.example/a b", None),
        ("img", "src", "/blog-media/a/b.png", "/blog-media/a/b.png"),
        ("img", "src", "https://images.example/x.png", "https://images.example/x.png"),
        ("img", "src", "mailto:a@b.example", None),
        ("img", "width", "1200", "1200"),
        ("img", "width", "0", None),
        ("img", "height", "100%", None),
        ("h2", "id", "a-b-c", "a-b-c"),
        ("h2", "id", "__proto__", None),
        ("h4", "id", "a", None),
        ("li", "id", "fn12", "fn12"),
        ("a", "id", "fnref3:1", "fnref3:1"),
        ("a", "id", "anything", None),
        ("span", "class", "k nf evil", "k nf"),
        ("span", "class", "evil-class", None),
        ("code", "class", "language-c++", "language-c++"),
        ("code", "class", "language-<x>", None),
        ("aside", "class", "callout callout-tip", "callout callout-tip"),
        ("aside", "class", "callout callout-danger", "callout"),
        ("td", "style", "text-align: center;", "text-align:center"),
        ("td", "style", "text-align:center;position:fixed", None),
        ("input", "type", "text", None),
        ("figure", "data-diagram", "a" * 64, "a" * 64),
        ("figure", "data-diagram", "a" * 63, None),
        ("ol", "start", "12", "12"),
        ("ol", "start", "-1", None),
        ("p", "onclick", "alert(1)", None),
    ],
)
def test_attribute_rules(tag: str, attribute: str, value: str, expected: str | None) -> None:
    assert attribute_filter(tag, attribute, value) == expected


def test_every_pygments_class_is_allowed() -> None:
    classes = {name for name in STANDARD_TYPES.values() if name}
    assert all(PYGMENTS_CLASS_RE.fullmatch(name) for name in classes)


def test_heading_ids_only_on_h2_h3() -> None:
    assert (
        sanitize_html('<h2 id="a">x</h2><h3 id="b">y</h3><h4 id="c">z</h4>')
        == '<h2 id="a">x</h2><h3 id="b">y</h3><h4>z</h4>'
    )


def test_profile_matches_the_shared_json() -> None:
    """spikes/editor-dialect/sanitizer-profile.json is the contract for the rehype-sanitize twin."""
    assert_golden(
        SPIKE_DIR / "sanitizer-profile.json", json.dumps(sanitizer_profile(), indent=2) + "\n"
    )
