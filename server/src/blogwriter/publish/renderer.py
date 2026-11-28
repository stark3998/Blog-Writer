"""Server-side rendering of the article dialect (plan: "Article pipeline, gates, publishing" >
Publish, steps 1-2).

The dialect parser (:mod:`.parser`: markdown-it-py ``commonmark`` with ``html: False``, GFM tables
and task lists, ``[^src-id]`` citations as footnotes, strict ``:::note|tip|warning`` callouts)
plus the rendering contract of ``docs/v2/article-dialect.md`` section 7:

- ``<h2 id>``/``<h3 id>``: ASCII kebab-case ids, de-duplicated (``-1``, ``-2``); they are also
  the table of contents;
- ``<pre class="highlight"><code class="language-x">`` with Pygments token classes;
- Mermaid fences: with a ``diagram_url`` hook, ``<figure class="mermaid" data-diagram="<sha256>">``
  around an ``<img>`` whose ``src`` is the URL the hook returns for the diagram's source; without
  one, the same figure around the escaped source (``<pre class="mermaid-source">``);
- ``<aside class="callout callout-note|tip|warning">``;
- citations as numbered footnote references with a ``<section class="footnotes">`` at the end;
- images get ``width``/``height`` from the asset manifest (``image_sizes``);
- a linked image (``[![alt](src)](href)``) is not part of the dialect: the image loses the link
  and any text around it keeps it (the editor reads it the same way), and the linter reports
  ``linked-image``.

The HTML is then sanitized with the shared nh3 profile (:mod:`.sanitizer`), so
``RenderedArticle.html`` is the ``htmlContent`` fragment of a published-blogs v2 document.

Rendering is CPU-bound (and slow for hostile input near the 512 KB cap): call
:func:`render_article` through ``asyncio.to_thread`` inside a job timeout.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from functools import cache
from typing import Final, cast

from markdown_it import MarkdownIt
from markdown_it.common.utils import escapeHtml, unescapeAll
from markdown_it.renderer import RendererHTML, RendererProtocol
from markdown_it.rules_core import StateCore
from markdown_it.token import Token
from markdown_it.utils import EnvType, OptionsDict
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexer import Lexer
from pygments.lexers import get_lexer_by_name  # pyright: ignore[reportUnknownVariableType]
from pygments.util import ClassNotFound

from .dialect import (
    CALLOUT_TYPES,
    LANGUAGE_RE,
    MERMAID_LANGUAGE,
    TOC_HEADING_LEVELS,
    check_source_size,
    info_language,
    normalize_source,
)
from .frontmatter import Frontmatter, parse_frontmatter, split_frontmatter
from .parser import new_dialect_markdown
from .profile import default_profile_rules
from .sanitizer import sanitize_html
from .slug import Slugger

__all__ = [
    "DEFAULT_DIAGRAM_ALT",
    "WORDS_PER_MINUTE",
    "Citation",
    "DiagramUrl",
    "DiagramUrlError",
    "Heading",
    "RenderedArticle",
    "TocEntry",
    "create_markdown",
    "diagram_id",
    "render_article",
]

WORDS_PER_MINUTE: Final = 230
DEFAULT_DIAGRAM_ALT: Final = "Diagram"
_ACC_TITLE_RE: Final = re.compile(r"^[ \t]*accTitle[ \t]*:[ \t]*(\S.*?)[ \t]*$", re.MULTILINE)
_PLAIN_ALT_RE: Final = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029<]")

type DiagramUrl = Callable[[str], str]
"""Maps a Mermaid diagram's source to the URL of its rendered image (for example
``/blog-media/<slug>/diagram-<sha>.svg``). The URL must pass the profile's ``src`` rule."""


@dataclass(frozen=True, slots=True)
class TocEntry:
    """A table-of-contents entry: an h2 or h3 and its id (``published-blogs`` ``toc[]``)."""

    level: int
    id: str
    text: str


@dataclass(frozen=True, slots=True)
class Heading:
    """Any heading of the body, in document order; ``id`` is set for h2/h3 only."""

    level: int
    text: str
    id: str | None


@dataclass(frozen=True, slots=True)
class Citation:
    """A cited source, numbered by first citation (the footnote number readers see).

    ``id`` is the ``[^src-id]`` label (the brief's source id). ``text`` is the definition's plain
    text and ``url`` its first link that the profile's ``href`` rule allows (``None`` without
    one): this metadata skips nh3, so the rule is applied here.
    """

    number: int
    id: str
    text: str
    url: str | None


@dataclass(frozen=True, slots=True)
class RenderedArticle:
    """The renderer's output. ``html`` is sanitized and ready to publish as ``htmlContent``."""

    html: str
    toc: tuple[TocEntry, ...]
    headings: tuple[Heading, ...]
    citations: tuple[Citation, ...]
    word_count: int
    reading_time_minutes: int
    frontmatter: Frontmatter | None
    mermaid_sources: tuple[str, ...]
    """The source of every Mermaid diagram, in document order (duplicates included)."""
    title: str | None
    """The frontmatter title (``None`` without frontmatter)."""
    excerpt: str | None
    """The frontmatter excerpt (``None`` without frontmatter)."""


class DiagramUrlError(ValueError):
    """The ``diagram_url`` hook returned a URL the sanitizer profile would strip."""


def diagram_id(source: str) -> str:
    """The ``data-diagram`` value of a Mermaid diagram: the SHA-256 of its source."""
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def render_article(
    markdown: str,
    *,
    diagram_url: DiagramUrl | None = None,
    image_sizes: Mapping[str, tuple[int, int]] | None = None,
) -> RenderedArticle:
    """Render an article (frontmatter optional) to sanitized HTML plus metadata.

    ``diagram_url`` maps a Mermaid diagram's source to its image URL; without it diagrams render
    as escaped source. ``image_sizes`` maps an image ``src`` (diagram URLs included) to
    ``(width, height)`` from the asset manifest, so known images get ``width``/``height``.

    Raises :class:`~.dialect.SourceTooLargeError` above 512 KB,
    :class:`~.frontmatter.FrontmatterError` for invalid frontmatter, :class:`DiagramUrlError`
    when the hook returns a URL outside the profile, and
    :class:`~blogwriter.publish.contract.ContractUnavailableError` when the sanitizer profile
    cannot be loaded.
    """
    check_source_size(markdown)
    frontmatter_text, body = split_frontmatter(normalize_source(markdown))
    frontmatter = parse_frontmatter(frontmatter_text) if frontmatter_text is not None else None

    md = create_markdown()
    env: EnvType = {"image_sizes": dict(image_sizes or {}), "diagram_url": diagram_url}
    tokens = md.parse(body, env)
    raw_html = cast("str", md.renderer.render(tokens, md.options, env))
    words = _count_words(tokens)
    return RenderedArticle(
        html=sanitize_html(raw_html),
        toc=tuple(cast("list[TocEntry]", env.get("toc", []))),
        headings=tuple(cast("list[Heading]", env.get("headings", []))),
        citations=_citations(tokens),
        word_count=words,
        reading_time_minutes=max(1, math.ceil(words / WORDS_PER_MINUTE)) if words else 0,
        frontmatter=frontmatter,
        mermaid_sources=tuple(cast("list[str]", env.get("mermaid_sources", []))),
        title=frontmatter.title if frontmatter is not None else None,
        excerpt=frontmatter.excerpt if frontmatter is not None else None,
    )


@cache
def create_markdown() -> MarkdownIt:
    """The renderer's markdown-it-py instance (reentrant; options travel in ``env``).

    ``create_markdown().render(body, env)`` returns **unsanitized** HTML, for audits and tests;
    publish only what :func:`render_article` returns.
    """
    md = new_dialect_markdown()
    md.core.ruler.after("inline", "unlink_images", _unlink_images)
    md.core.ruler.push("heading_ids", _heading_ids)
    for kind in CALLOUT_TYPES:
        md.add_render_rule(f"container_{kind}_open", _render_callout)
        md.add_render_rule(f"container_{kind}_close", _render_callout)
    md.add_render_rule("image", _image_renderer(md))
    md.add_render_rule("fence", _render_fence)
    md.add_render_rule("code_block", _render_code_block)
    return md


def _image_renderer(md: MarkdownIt) -> Callable[..., str]:
    if not isinstance(md.renderer, RendererHTML):
        raise TypeError("the dialect renders with markdown-it's RendererHTML")
    default_image = cast(
        "Callable[[Sequence[Token], int, OptionsDict, EnvType], str]", md.renderer.rules["image"]
    )

    def render_image(
        renderer: RendererProtocol,
        tokens: Sequence[Token],
        idx: int,
        options: OptionsDict,
        env: EnvType,
    ) -> str:
        token = tokens[idx]
        size = _image_size(env, str(token.attrGet("src") or ""))
        if size is not None:
            token.attrSet("width", str(size[0]))
            token.attrSet("height", str(size[1]))
        return default_image(tokens, idx, options, env)

    return render_image


def _image_size(env: EnvType, src: str) -> tuple[int, int] | None:
    sizes = cast("Mapping[str, tuple[int, int]]", env.get("image_sizes") or {})
    size = sizes.get(src)
    if size is None:
        return None
    return int(size[0]), int(size[1])


def _render_callout(
    renderer: RendererProtocol,
    tokens: Sequence[Token],
    idx: int,
    options: OptionsDict,
    env: EnvType,
) -> str:
    token = tokens[idx]
    if token.nesting == 1:
        kind = token.info.strip()
        return f'<aside class="callout callout-{escapeHtml(kind)}">\n'
    return "</aside>\n"


_FORMATTER: Final[HtmlFormatter[str]] = HtmlFormatter(nowrap=True)


def _lexer(language: str) -> Lexer | None:
    if not language:
        return None
    try:
        lexer = get_lexer_by_name(language, stripnl=False, ensurenl=False)
    except ClassNotFound:
        return None
    return lexer


def _code_html(code: str, language: str) -> str:
    lexer = _lexer(language)
    body = highlight(code, lexer, _FORMATTER) if lexer is not None else escapeHtml(code)
    class_attr = (
        f' class="language-{escapeHtml(language)}"' if LANGUAGE_RE.fullmatch(language) else ""
    )
    return f'<pre class="highlight"><code{class_attr}>{body}</code></pre>\n'


def _render_fence(
    renderer: RendererProtocol,
    tokens: Sequence[Token],
    idx: int,
    options: OptionsDict,
    env: EnvType,
) -> str:
    token = tokens[idx]
    language = info_language(unescapeAll(token.info))
    if language == MERMAID_LANGUAGE:
        return _render_diagram(token.content, env)
    return _code_html(token.content, language)


def _render_diagram(source: str, env: EnvType) -> str:
    cast("list[str]", env.setdefault("mermaid_sources", [])).append(source)
    digest = diagram_id(source)
    hook = cast("DiagramUrl | None", env.get("diagram_url"))
    if hook is None:
        return (
            f'<figure class="mermaid" data-diagram="{digest}">'
            f'<pre class="mermaid-source"><code>{escapeHtml(source)}</code></pre>'
            "</figure>\n"
        )
    url = hook(source)
    if not isinstance(url, str) or not default_profile_rules().allowed_src(url):  # pyright: ignore[reportUnnecessaryIsInstance]
        raise DiagramUrlError("diagram_url returned a URL outside the sanitizer profile's src rule")
    size = _image_size(env, url)
    dimensions = f' width="{size[0]}" height="{size[1]}"' if size is not None else ""
    return (
        f'<figure class="mermaid" data-diagram="{digest}">'
        f'<img src="{escapeHtml(url)}" alt="{escapeHtml(_diagram_alt(source))}"{dimensions}>'
        "</figure>\n"
    )


def _diagram_alt(source: str) -> str:
    """Mermaid's own accessible title (``accTitle: ...``) when the diagram has one."""
    match = _ACC_TITLE_RE.search(source)
    if match is None:
        return DEFAULT_DIAGRAM_ALT
    title = _PLAIN_ALT_RE.sub(" ", match.group(1)).strip()
    return title[:300] if title else DEFAULT_DIAGRAM_ALT


def _render_code_block(
    renderer: RendererProtocol,
    tokens: Sequence[Token],
    idx: int,
    options: OptionsDict,
    env: EnvType,
) -> str:
    return _code_html(tokens[idx].content, "")


def _inline_text(children: Sequence[Token] | None) -> str:
    parts: list[str] = []
    for child in children or ():
        if child.type in ("text", "code_inline", "text_special"):
            parts.append(child.content)
        elif child.type in ("softbreak", "hardbreak"):
            parts.append(" ")
        elif child.type == "image":
            parts.append(_inline_text(child.children))
    return "".join(parts)


def _unlink_images(state: StateCore) -> None:
    """A linked image (``[![alt](src)](href)``) loses its link, as in the editor: the link is
    closed before the image and reopened after it, and links left empty are dropped."""
    for token in state.tokens:
        if token.type == "inline" and token.children:
            token.children = _without_image_links(token.children)


def _without_image_links(children: list[Token]) -> list[Token]:
    if not any(child.type == "image" for child in children):
        return children
    split: list[Token] = []
    link: Token | None = None
    for child in children:
        if child.type == "link_open":
            link = child
        elif child.type == "link_close":
            link = None
        elif child.type == "image" and link is not None:
            split.append(Token("link_close", "a", -1, markup=link.markup, info=link.info))
            split.append(child)
            reopened = Token("link_open", "a", 1, markup=link.markup, info=link.info)
            reopened.attrs = dict(link.attrs)
            split.append(reopened)
            continue
        split.append(child)
    out: list[Token] = []
    for child in split:
        if child.type == "link_close" and out and out[-1].type == "link_open":
            out.pop()
            continue
        out.append(child)
    return out


def _heading_ids(state: StateCore) -> None:
    """Give h2/h3 an id (inside the body, not in footnotes); collect the TOC and all headings."""
    slugger = Slugger()
    toc: list[TocEntry] = []
    headings: list[Heading] = []
    in_footnotes = False
    for index, token in enumerate(state.tokens):
        if token.type == "footnote_block_open":
            in_footnotes = True
        elif token.type == "footnote_block_close":
            in_footnotes = False
        if token.type != "heading_open" or in_footnotes:
            continue
        level = int(token.tag[1])
        text = _inline_text(state.tokens[index + 1].children).strip()
        if level not in TOC_HEADING_LEVELS:
            headings.append(Heading(level, text, None))
            continue
        heading_id = slugger.slug(text)
        token.attrSet("id", heading_id)
        toc.append(TocEntry(level, heading_id, text))
        headings.append(Heading(level, text, heading_id))
    state.env["toc"] = toc
    state.env["headings"] = headings


def _citations(tokens: Sequence[Token]) -> tuple[Citation, ...]:
    rules = default_profile_rules()
    citations: list[Citation] = []
    for index, token in enumerate(tokens):
        if token.type != "footnote_open":
            continue
        label = str(token.meta.get("label") or "")
        number = int(token.meta["id"]) + 1
        text_parts: list[str] = []
        url: str | None = None
        for inner in tokens[index + 1 :]:
            if inner.type == "footnote_close":
                break
            if inner.type != "inline":
                continue
            text_parts.append(_inline_text(inner.children))
            for child in inner.children or ():
                if child.type == "link_open" and url is None:
                    href = str(child.attrGet("href") or "")
                    # markdown-it alone lets http:, //host and relative links through.
                    if rules.allowed_href(href):
                        url = href
        citations.append(Citation(number, label, " ".join(text_parts).strip(), url))
    return tuple(citations)


def _count_words(tokens: Sequence[Token]) -> int:
    """Words of prose: inline text outside code blocks and the footnotes section."""
    count = 0
    in_footnotes = False
    for token in tokens:
        if token.type == "footnote_block_open":
            in_footnotes = True
        elif token.type == "footnote_block_close":
            in_footnotes = False
        elif token.type == "inline" and not in_footnotes:
            count += len(_inline_text(token.children).split())
    return count
