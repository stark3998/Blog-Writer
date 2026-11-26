"""Frontmatter twin tests, plus the cross-language check against the TipTap goldens."""

from __future__ import annotations

import pytest

from article_render.dialect import normalize_source
from article_render.frontmatter import (
    Frontmatter,
    FrontmatterError,
    Series,
    dump_frontmatter,
    parse_frontmatter,
    quote_yaml_string,
    split_frontmatter,
)

from .conftest import CORPUS_DIR, CORPUS_FILES, NORMALIZED_DIR

VALID = """title: "Least-privilege tool calls"
slug: least-privilege-tool-calls
excerpt: "How to scope one tool call."
pillar: agents
format: deep-explainer
tags: [agents, identity]
"""


def issues_of(text: str) -> list[str]:
    try:
        parse_frontmatter(text)
    except FrontmatterError as error:
        return [f"{issue.path}: {issue.message}" for issue in error.issues]
    return []


def test_valid() -> None:
    assert parse_frontmatter(VALID) == Frontmatter(
        "Least-privilege tool calls",
        "least-privilege-tool-calls",
        "How to scope one tool call.",
        "agents",
        "deep-explainer",
        ("agents", "identity"),
    )


def test_yaml_11_lookalikes_stay_strings() -> None:
    fm = parse_frontmatter(
        VALID.replace('"Least-privilege tool calls"', "no").replace(
            "[agents, identity]", "[yes, on, null]"
        )
    )
    assert fm.title == "no"
    assert fm.tags == ("yes", "on", "null")


@pytest.mark.parametrize(
    ("yaml_text", "issue"),
    [
        (VALID + "author: me\n", "author: unknown key"),
        (VALID.replace("pillar: agents\n", ""), "pillar: required"),
        (
            VALID.replace("Least-privilege tool calls", "<b>x</b>"),
            'title: must not contain "<" (plain text only)',
        ),
        (VALID.replace("[agents, identity]", "[agents, agents]"), "tags[1]: duplicate tag"),
        (
            VALID + "series: {id: a, title: T, part: 3, total: 2}\n",
            "series.total: must be greater than or equal to series.part",
        ),
        (
            VALID.replace('"Least-privilege tool calls"', '"a\\nb"'),
            "title: must not contain control characters or line breaks",
        ),
    ],
)
def test_invalid(yaml_text: str, issue: str) -> None:
    assert issue in issues_of(yaml_text)


def test_duplicate_keys_and_aliases_are_rejected() -> None:
    assert issues_of(VALID + "slug: other\n")[0].startswith("(frontmatter): YAML error")
    assert issues_of(VALID.replace("pillar: agents", "pillar: &p agents") + "format2: *p\n")[
        0
    ].startswith("(frontmatter): YAML error")


def test_dump_layout_and_round_trip() -> None:
    fm = Frontmatter(
        'Migrating SSO: "the boring way"',
        "okta-to-entra",
        "Back\\slash and unicode → ✓",
        "identity",
        "case-study",
        (),
        Series("okta-entra", "Okta → Entra", 2),
    )
    text = dump_frontmatter(fm)
    assert text == (
        "---\n"
        'title: "Migrating SSO: \\"the boring way\\""\n'
        "slug: okta-to-entra\n"
        'excerpt: "Back\\\\slash and unicode → ✓"\n'
        "pillar: identity\n"
        "format: case-study\n"
        "tags: []\n"
        "series:\n"
        "  id: okta-entra\n"
        '  title: "Okta → Entra"\n'
        "  part: 2\n"
        "---\n"
    )
    yaml_text, _ = split_frontmatter(text + "\nbody\n")
    assert yaml_text is not None
    assert parse_frontmatter(yaml_text) == fm


@pytest.mark.parametrize(
    "value",
    [
        "plain",
        'q"uote',
        "back\\slash",
        "tab\tx",
        "line\u2028sep",
        "\u0085nel",
        "emoji \U0001f510",
        "\x7f",
    ],
)
def test_quote_yaml_string_round_trips(value: str) -> None:
    import yaml

    assert yaml.load(f"v: {quote_yaml_string(value)}", Loader=yaml.BaseLoader) == {"v": value}  # noqa: S506


def _frontmatter_block(path_text: str) -> str | None:
    yaml_text, _ = split_frontmatter(normalize_source(path_text))
    return yaml_text


@pytest.mark.parametrize("name", CORPUS_FILES)
def test_python_canonical_frontmatter_equals_tiptap(name: str) -> None:
    """Both sides must write byte-identical canonical frontmatter."""
    source = (CORPUS_DIR / name).read_text(encoding="utf-8")
    yaml_text = _frontmatter_block(source)
    normalized = (NORMALIZED_DIR / name).read_text(encoding="utf-8")
    if yaml_text is None:
        assert _frontmatter_block(normalized) is None
        return
    canonical = dump_frontmatter(parse_frontmatter(yaml_text))
    assert normalized.startswith(canonical + "\n")


def test_explicit_tags_stay_strings() -> None:
    """Twin of the web test: both sides read tagged scalars as plain strings."""
    fm = parse_frontmatter(
        'title: !!binary aGk=\nslug: a\nexcerpt: "E"\npillar: !!timestamp 2024-01-01\n'
        "format: f\ntags: [a]\n"
    )
    assert (fm.title, fm.pillar) == ("aGk=", "2024-01-01")


def test_deep_nesting_is_invalid_frontmatter_not_a_crash() -> None:
    """Reviewer: ~3 KB of "[[[" raised RecursionError out of render_article and lint_article."""
    from article_render import render_article
    from article_render.lint import lint_article

    yaml_text = 'title: "T"\nslug: t\nexcerpt: "E"\npillar: p\nformat: f\ntags: []\n'
    yaml_text += "series: " + "[" * 3000 + "\n"
    with pytest.raises(FrontmatterError, match="nested too deeply"):
        parse_frontmatter(yaml_text)
    with pytest.raises(FrontmatterError):
        render_article(f"---\n{yaml_text}---\n\nBody.\n")
    assert [f.code for f in lint_article(f"---\n{yaml_text}---\n\nBody.\n")] == [
        "frontmatter-invalid"
    ]
