"""The nh3 sanitizer built from ``contracts/sanitizer-profile.json``: value rules, the profile
loader (fail closed) and consistency with the contract's audit."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any, Final

import pytest
from pygments.token import STANDARD_TYPES

from blogwriter.publish.contract import (
    PROFILE_FILE,
    ContractUnavailableError,
    PublishedContract,
    SanitizerProfile,
)
from blogwriter.publish.profile import ProfileRules, default_profile_rules, load_profile
from blogwriter.publish.sanitizer import URL_SCHEMES, attribute_filter, build_cleaner, sanitize_html

REPO_DIR: Final = Path(__file__).resolve().parents[3]
CONTRACTS_DIR: Final = REPO_DIR / "contracts"


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
        ("a", "href", "https://ok.example/a\x01b", None),
        ("a", "href", "https://ok.example/a\x7fb", None),
        ("a", "href", "javascript:alert(1)", None),
        ("img", "src", "/blog-media/a/b.png", "/blog-media/a/b.png"),
        ("img", "src", "https://images.example/x.png", "https://images.example/x.png"),
        ("img", "src", "mailto:a@b.example", None),
        ("img", "src", "#frag", None),
        ("img", "src", "data:image/png;base64,AAAA", None),
        ("img", "width", "1200", "1200"),
        ("img", "width", "0", None),
        ("img", "width", "123456", None),
        ("img", "height", "100%", None),
        ("img", "alt", "any <text>", "any <text>"),
        ("h2", "id", "a-b-c", "a-b-c"),
        ("h2", "id", "__proto__", None),
        ("h2", "id", "a-b\n", None),
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
        ("input", "checked", "", "checked"),
        ("figure", "data-diagram", "a" * 64, "a" * 64),
        ("figure", "data-diagram", "a" * 63, None),
        ("ol", "start", "12", "12"),
        ("ol", "start", "-1", None),
        ("p", "onclick", "alert(1)", None),
        ("a", "rel", "noopener noreferrer", "noopener noreferrer"),
        ("a", "rel", "opener", None),
    ],
)
def test_attribute_rules(tag: str, attribute: str, value: str, expected: str | None) -> None:
    assert attribute_filter(tag, attribute, value) == expected


def test_every_pygments_class_is_allowed() -> None:
    classes = {name for name in STANDARD_TYPES.values() if name}
    pattern = default_profile_rules().profile.class_patterns["span"]
    assert all(pattern.search(name) for name in classes)


def test_heading_ids_only_on_h2_h3() -> None:
    assert (
        sanitize_html('<h2 id="a">x</h2><h3 id="b">y</h3><h4 id="c">z</h4>')
        == '<h2 id="a">x</h2><h3 id="b">y</h3><h4>z</h4>'
    )


def test_forced_checkbox_and_link_rel() -> None:
    assert sanitize_html('<input type="text" checked onclick="x()">') == (
        '<input type="checkbox" checked="checked" disabled="disabled">'
    )
    assert sanitize_html('<a href="#x" rel="opener" target="_blank">x</a>') == (
        '<a href="#x" rel="noopener noreferrer">x</a>'
    )


def test_dropped_with_content() -> None:
    assert sanitize_html("<p>a<script>alert(1)</script><style>p{}</style>b</p>") == "<p>ab</p>"


def test_cleaner_follows_the_shared_profile(contract: PublishedContract) -> None:
    """The sanitizer applies the contract's profile, which the HTML audit also enforces."""
    assert default_profile_rules().profile == contract.profile


def test_url_schemes_match_the_profile_patterns() -> None:
    """nh3's scheme allowlist must not be narrower or wider than the profile's URL patterns."""
    raw: dict[str, Any] = json.loads((CONTRACTS_DIR / PROFILE_FILE).read_text(encoding="utf-8"))
    schemes = {
        match.group(1)
        for patterns in raw["urlPatterns"].values()
        for pattern in patterns
        if (match := re.match(r"\^([a-z]+):", pattern))
    }
    assert schemes == set(URL_SCHEMES)


def test_build_cleaner_with_explicit_rules() -> None:
    cleaner = build_cleaner(ProfileRules(load_profile(CONTRACTS_DIR)))
    assert cleaner.clean('<p onclick="x">a</p><iframe src="x"></iframe>') == "<p>a</p>"


def _profile_copy(tmp_path: Path, **changes: object) -> Path:
    shutil.copy(CONTRACTS_DIR / PROFILE_FILE, tmp_path / PROFILE_FILE)
    if changes:
        data: dict[str, Any] = json.loads((tmp_path / PROFILE_FILE).read_text(encoding="utf-8"))
        data.update(changes)
        (tmp_path / PROFILE_FILE).write_text(json.dumps(data), encoding="utf-8")
    return tmp_path


def test_load_profile_from_a_directory(tmp_path: Path) -> None:
    profile = load_profile(_profile_copy(tmp_path))
    assert isinstance(profile, SanitizerProfile)
    assert profile.version == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"version": 2},
        {"tags": ["a", "script"], "dropWithContent": ["script"]},
        {"attributes": {"p": ["onclick"]}},
    ],
)
def test_unusable_profile_fails_closed(tmp_path: Path, changes: dict[str, object]) -> None:
    with pytest.raises(ContractUnavailableError):
        load_profile(_profile_copy(tmp_path, **changes))


def test_missing_or_malformed_profile_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(ContractUnavailableError):
        load_profile(tmp_path)
    (tmp_path / PROFILE_FILE).write_text('{"version": 1, "version": 1}', encoding="utf-8")
    with pytest.raises(ContractUnavailableError, match="not valid JSON"):
        load_profile(tmp_path)
