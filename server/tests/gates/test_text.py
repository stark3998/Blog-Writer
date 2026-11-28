"""Prose views keep offsets: blocks, masking, sentences and words."""

from __future__ import annotations

from blogwriter.gates.text import markdown_view, plain_view, sentences, words

BODY = """Intro with a [link](https://example.com/a?b=1) and `code` here.[^src-1] See fig. 3.

## A heading

- item one, two, and three
- item **two** \\*escaped\\*

> quoted text

:::note
Callout text.
:::

| a | b |
| --- | --- |
| 1 | 2 |

```mermaid
graph TD; A-->B
```

![Alt text](/blog-media/slug/x.png)

[^src-1]: Publisher. *Title*. [site](https://example.com/b)
"""


def test_markdown_view_keeps_length_and_masks_syntax() -> None:
    view = markdown_view(BODY)
    assert len(view.text) == len(BODY)
    assert [view.text[i] for i, char in enumerate(BODY) if char == "\n"] == ["\n"] * BODY.count(
        "\n"
    )
    for hidden in ("https://example.com", "[^src-1]", "graph TD", "Alt text", "Publisher", "| a"):
        assert hidden not in view.text
    assert "code" not in view.text  # inline code is a specific, not prose
    assert view.code_spans == ((BODY.index("code"), BODY.index("code") + 4),)
    kinds = [block.kind for block in view.blocks]
    assert kinds[:6] == ["paragraph", "heading", "list_item", "list_item", "quote", "callout"]
    assert "other" in kinds
    first = view.blocks[0]
    assert BODY[first.start : first.start + 10] == "Intro with"
    assert view.block_text(view.blocks[1]) == "A heading"
    assert view.block_text(view.blocks[3]).split() == ["item", "two", "*escaped", "*"]


def test_sentences_respect_abbreviations_and_offsets() -> None:
    text = "The St. Louis team moved 2,000 apps, e.g. the HR ones. Did it work? Mostly… It did."
    found = sentences(text, offset=10)
    assert [sentence.text for sentence in found] == [
        "The St. Louis team moved 2,000 apps, e.g. the HR ones.",
        "Did it work?",
        "Mostly…",
        "It did.",
    ]
    assert found[1].start == 10 + text.index("Did")
    assert found[0].words == 11
    assert sentences("   ") == []


def test_words_keep_numbers_versions_and_hosts_together() -> None:
    assert [word.text for word in words("We moved 2,000 apps to v1.2 at api.example.com.")] == [
        "We",
        "moved",
        "2,000",
        "apps",
        "to",
        "v1.2",
        "at",
        "api.example.com",
    ]


def test_plain_view_reads_linkedin_line_breaks_and_bullets() -> None:
    text = (
        "I moved 2,000 apps.\nIt broke.\nWe fixed it.\n\n- one\n✅ two\n\n"
        "A paragraph that wraps\nacross lines without ending"
    )
    view = plain_view(text)
    assert [(block.kind, view.block_text(block)) for block in view.blocks] == [
        ("paragraph", "I moved 2,000 apps."),
        ("paragraph", "It broke."),
        ("paragraph", "We fixed it."),
        ("list_item", "one"),
        ("list_item", "two"),
        ("paragraph", "A paragraph that wraps\nacross lines without ending"),
    ]
    assert view.text == text


def test_masking_stays_linear_on_hostile_lines() -> None:
    # A quadratic link pattern took minutes on this; linear takes well under a second.
    for line in ("[" * 30_000, "![a](b" * 3_000, "`a" * 20_000, "*_" * 20_000):
        view = markdown_view(line + "\n")
        assert len(view.text) == len(line) + 1
