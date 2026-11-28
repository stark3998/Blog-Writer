"""Cleaning drafts, binding citations to the brief, normalization and the small text helpers."""

from __future__ import annotations

from blogwriter.domain.common import Citation
from blogwriter.pipeline.testing import CITATION_ID, SOURCE_URL, draft_body, sample_brief
from blogwriter.pipeline.text import (
    bind_citations,
    chunks,
    citation_definition,
    citation_refs,
    clean_draft,
    dialect_errors,
    existing_definitions,
    mermaid_sources,
    normalize_body,
    plain_text,
    slug_of,
    tag_slugs,
)

CITATIONS = sample_brief().citations


def test_clean_draft_drops_the_model_habits() -> None:
    body = clean_draft(draft_body())
    assert not body.startswith("```")
    assert not body.startswith("# ")
    assert "## References" not in body
    assert "## Scope the token to the call" in body  # a real section survives
    assert "flowchart LR" in body  # the diagram's fence is not the wrapper
    assert body.endswith("\n")
    assert not body.endswith("\n\n")


def test_clean_draft_keeps_code_that_looks_like_a_heading() -> None:
    text = "Intro.\n\n```bash\n# References\necho hi\n```\n\n## Next\n\nMore."
    body = clean_draft(text)
    assert "# References" in body
    assert body.count("```") == 2


def test_clean_draft_strips_frontmatter() -> None:
    text = '---\ntitle: "x"\nslug: x\n---\nBody text.\n'
    assert clean_draft(text) == "Body text.\n"


def test_citations_come_from_the_brief_only() -> None:
    bound = bind_citations(clean_draft(draft_body()), CITATIONS)
    assert bound.cited == (CITATION_ID,)
    assert bound.removed == 1  # [^src-made-up]
    assert "src-made-up" not in bound.text
    assert "example.com/fake" not in bound.text  # the model's own definition is gone
    definition = citation_definition(CITATIONS[0])
    assert bound.text.rstrip("\n").endswith(definition)
    assert bound.text.count(f"[^{CITATION_ID}]:") == 1
    assert "do this already." in bound.text  # the sentence stays, its invented source goes


def test_citation_definition_golden() -> None:
    assert citation_definition(CITATIONS[0]) == (
        f"[^{CITATION_ID}]: Microsoft Learn. *Agent identities in Microsoft Entra*. "
        f"[learn.microsoft.com]({SOURCE_URL})"
    )
    odd = Citation(
        id="src-odd",
        url="https://en.example.org/wiki/Token_(security)",
        title="Tokens *and* [brackets]",
        domain="en.example.org",
    )
    line = citation_definition(odd)
    assert line == (
        r"[^src-odd]: *Tokens \*and\* \[brackets\]*. "
        "[en.example.org](<https://en.example.org/wiki/Token_(security)>)"
    )


def test_references_inside_code_are_not_citations() -> None:
    body = "Text.[^src-a]\n\n```\nlist[^src-b]\n```\n\nInline `x[^src-c]` too.\n"
    cites = [Citation(id="src-a", url="https://a.example.com/x", title="A", domain="a.example.com")]
    bound = bind_citations(body, cites)
    assert "list[^src-b]" in bound.text
    assert "`x[^src-c]`" in bound.text
    assert citation_refs(bound.text) == ["src-a"]


def test_an_owners_own_sources_survive_a_revision() -> None:
    before = "Old.[^own-note]\n\n[^own-note]: My notes. *Field log*. [example.net](https://example.net/x)\n"
    kept = existing_definitions(before)
    assert set(kept) == {"own-note"}
    after = "New wording.[^own-note] And a fact.[^" + CITATION_ID + "]\n"
    bound = bind_citations(after, CITATIONS, keep=kept)
    assert kept["own-note"] in bound.text
    assert bound.cited == (CITATION_ID,)
    assert bound.removed == 0


def test_normalize_body_is_canonical_and_stable() -> None:
    raw = "Intro paragraph\nwith a soft break.\n\n* one\n* two\n\n---\n\nAfter the break.\n"
    first = normalize_body(raw)
    assert "- one" in first.text
    assert "Intro paragraph with a soft break." in first.text
    assert first.errors == ()
    again = normalize_body(first.text)
    assert again.text == first.text


def test_a_body_that_opens_with_a_break_is_not_frontmatter() -> None:
    result = normalize_body("---\n\nA paragraph after a break.\n")
    assert "A paragraph after a break." in result.text


def test_normalizer_errors_point_at_body_lines() -> None:
    result = normalize_body("Line one.\n\nA claim.[^nowhere]\n")
    codes = {error.code for error in result.errors}
    assert "citation-undefined" in codes
    lines = {error.line for error in result.errors if error.code == "citation-undefined"}
    assert lines == {3}


def test_dialect_errors_name_unknown_sources() -> None:
    body = f"A claim.[^{CITATION_ID}]\n\n[^{CITATION_ID}]: X. *Y*. [a.example.com](https://a.example.com)\n"
    assert dialect_errors(body, [CITATION_ID]) == ()
    codes = {issue.code for issue in dialect_errors(body, ["src-other"])}
    assert "citation-unknown-source" in codes


def test_mermaid_sources_in_order() -> None:
    body = (
        "```mermaid\nflowchart LR\n  A --> B\n```\n\n```python\nx = 1\n```\n\n"
        "```mermaid\nsequenceDiagram\n  A->>B: hi\n```\n"
    )
    sources = mermaid_sources(body)
    assert [source.split("\n")[0] for source in sources] == ["flowchart LR", "sequenceDiagram"]


def test_small_helpers() -> None:
    assert plain_text('  "A <b>title</b>\nwith a break"  ', 150) == "A btitle/b with a break"
    assert slug_of("Scope Agent Tokens: Per Tool Call!") == "scope-agent-tokens-per-tool-call"
    assert slug_of("") is None
    assert slug_of("日本語") is None
    assert len(slug_of("word " * 40) or "") <= 80
    assert tag_slugs(["Entra ID", "entra-id", "Least privilege", "", "a" * 60]) == [
        "entra-id",
        "least-privilege",
        "a" * 40,
    ]
    assert [len(piece) for piece in chunks("x" * 9000)] == [4000, 4000, 1000]
