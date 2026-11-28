"""v1 Markdown into the dialect: frontmatter, MDX, headings, images; code is never touched."""

from __future__ import annotations

from blogwriter.backups.migrate_v1.markdown import (
    Image,
    clean_v1_markdown,
    find_images,
    referenced_v1_images,
    rewrite_images,
    split_v1_frontmatter,
    take_leading_image,
    v1_image_id,
    v1_tags,
)


def test_v1_frontmatter_is_read_like_v1_did() -> None:
    meta, body = split_v1_frontmatter(
        '---\r\ntitle: "A: title"\r\ntags: ["One", "two words"]\r\n'
        "excerpt: 'x'\r\n---\r\nBody\r\n"
    )
    assert meta == {"title": "A: title", "tags": '["One", "two words"]', "excerpt": "x"}
    assert body == "Body\n"
    assert v1_tags(meta["tags"]) == ["One", "two words"]
    assert split_v1_frontmatter("No frontmatter\n") == ({}, "No frontmatter\n")


def test_mdx_and_jsx_are_removed_outside_code_but_text_is_kept() -> None:
    body = (
        'import Callout from "./callout"\n'
        "export const meta = {}\n\n"
        '<Callout type="tip">Keep **this** text.</Callout>\n\n'
        "Use `<Button />` inline.\n\n"
        "```tsx\nimport X from 'y'\n<Button />\n```\n"
    )
    cleaned = clean_v1_markdown(body, title=None)
    assert "import Callout" not in cleaned
    assert "export const" not in cleaned
    assert "Keep **this** text." in cleaned
    assert "<Callout" not in cleaned
    assert "`<Button />`" in cleaned
    assert "```tsx\nimport X from 'y'\n<Button />\n```" in cleaned


def test_headings_move_to_dialect_levels() -> None:
    body = (
        "# My Title\n\nIntro.\n\n# Another H1\n\n###### Too deep\n\n##### Also deep\n\n"
        "#\n\n## Fine ##\n\n```\n# comment in code\n```\n"
    )
    cleaned = clean_v1_markdown(body, title="My title")
    assert "My Title" not in cleaned  # the h1 repeating the title is the v1 banner
    assert "## Another H1" in cleaned
    assert "#### Too deep" in cleaned
    assert "#### Also deep" in cleaned
    assert "## Fine\n" in cleaned
    assert "# comment in code" in cleaned
    assert "\n#\n" not in cleaned


def test_an_h1_after_other_headings_is_kept_as_h2() -> None:
    cleaned = clean_v1_markdown("## First\n\n# My Title\n", title="My Title")
    assert "## My Title" in cleaned


def test_html_img_tags_become_markdown_images() -> None:
    cleaned = clean_v1_markdown(
        '<img src="/api/images/abc" alt="A [chart]">\n\n<img alt=\'x\' src=bad.png>\n'
        '<img src="javascript:alert(1)" alt="y">\n',
        title=None,
    )
    assert "![A \\[chart\\]](/api/images/abc)" in cleaned
    assert "![x](bad.png)" in cleaned
    assert "javascript" not in cleaned  # a source with parentheses is dropped


def test_find_and_rewrite_images() -> None:
    body = (
        "![Hero](/api/images/a)\n\nText ![data](data:image/png;base64,AAAA). "
        '![ext](https://example.com/x.png "title")\n\n'
        "`![code](/api/images/b)`\n\n```\n![fenced](/api/images/c)\n```\n"
    )
    assert find_images(body) == [
        Image("/api/images/a", "Hero"),
        Image("data:image/png;base64,AAAA", "data"),
        Image("https://example.com/x.png", "ext"),
    ]

    def resolve(image: Image) -> str | None:
        if image.src.startswith("data:"):
            return None
        if image.src == "/api/images/a":
            return "/blog-media/post/abc.jpg"
        return image.src

    rewritten = rewrite_images(body, resolve)
    assert "![Hero](/blog-media/post/abc.jpg)" in rewritten
    assert "Text." in rewritten
    assert "data:" not in rewritten
    assert '![ext](https://example.com/x.png "title")' in rewritten
    assert "`![code](/api/images/b)`" in rewritten
    assert "![fenced](/api/images/c)" in rewritten


def test_a_dropped_linked_image_leaves_no_empty_link() -> None:
    rewritten = rewrite_images("See [![x](data:image/png;base64,AA)](https://a.example) now.\n",
                               lambda image: None)  # fmt: skip
    assert rewritten == "See now.\n"


def test_the_leading_image_is_taken() -> None:
    image, rest = take_leading_image("\n![Hero shot](/api/images/h)\n\nFirst paragraph.\n")
    assert image == Image("/api/images/h", "Hero shot")
    assert rest == "First paragraph.\n"
    assert take_leading_image("Text ![x](/api/images/h)\n")[0] is None


def test_v1_image_ids() -> None:
    assert v1_image_id("/api/images/0f1e-22") == "0f1e-22"
    assert v1_image_id("https://writer.example.com/api/images/abc") == "abc"
    assert v1_image_id("/api/images/../x") is None
    assert v1_image_id("/images/abc") is None
    assert v1_image_id("https://example.com/a.png") is None


def test_referenced_images_skip_code() -> None:
    markdown = (
        '---\ntitle: "T"\n---\n![T](/api/images/hero)\n\n'
        "![b](/api/images/b) ![a](https://writer.example/api/images/a)\n\n"
        "```\n![c](/api/images/never)\n```\n"
    )
    assert referenced_v1_images(markdown) == ["a", "b", "hero"]
