"""Server-side rendering of the article dialect.

markdown-it-py ``commonmark`` preset with ``html: False``, plus GFM tables,
task lists (mdit_py_plugins.tasklists), citations as footnotes
(mdit_py_plugins.footnote, no inline footnotes) and callouts
(mdit_py_plugins.container, strict ``:::note|tip|warning``). Code blocks get
Pygments classes; mermaid fences become placeholder figures for the asset
pipeline; h2/h3 get ids and a table of contents. The HTML is then sanitized
with the shared nh3 profile.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from markdown_it import MarkdownIt
from markdown_it.common.utils import escapeHtml, unescapeAll
from markdown_it.rules_core import StateCore
from markdown_it.token import Token
from markdown_it.utils import EnvType, OptionsDict
from mdit_py_plugins.container import container_plugin
from mdit_py_plugins.footnote import footnote_plugin
from mdit_py_plugins.tasklists import tasklists_plugin
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_by_name
from pygments.util import ClassNotFound

from .dialect import (
    CALLOUT_TYPES,
    LANGUAGE_RE,
    MAX_SOURCE_BYTES,
    MERMAID_LANGUAGE,
    TOC_HEADING_LEVELS,
    info_language,
    normalize_source,
)
from .frontmatter import Frontmatter, parse_frontmatter, split_frontmatter
from .sanitizer import _allowed_href, sanitize_html
from .slug import Slugger

WORDS_PER_MINUTE = 230


@dataclass(frozen=True, slots=True)
class TocEntry:
    level: int
    id: str
    text: str


@dataclass(frozen=True, slots=True)
class Citation:
    number: int
    id: str
    text: str
    url: str | None


@dataclass(frozen=True, slots=True)
class Diagram:
    sha256: str
    source: str


@dataclass(frozen=True, slots=True)
class RenderResult:
    html: str
    frontmatter: Frontmatter | None
    toc: tuple[TocEntry, ...] = ()
    citations: tuple[Citation, ...] = ()
    diagrams: tuple[Diagram, ...] = ()
    word_count: int = 0
    reading_time_minutes: int = 0
    unsanitized_html: str = field(default="", repr=False)


class SourceTooLargeError(ValueError):
    pass


def render_article(
    source: str, *, image_sizes: Mapping[str, tuple[int, int]] | None = None
) -> RenderResult:
    """Render an article (frontmatter optional) to sanitized HTML plus metadata.

    ``image_sizes`` maps an image ``src`` to ``(width, height)`` from the asset
    manifest; known images get ``width``/``height`` attributes (no layout shift).
    """
    if len(source.encode("utf-8")) > MAX_SOURCE_BYTES:
        raise SourceTooLargeError(f"article is larger than {MAX_SOURCE_BYTES} bytes")

    frontmatter_text, body = split_frontmatter(normalize_source(source))
    frontmatter = parse_frontmatter(frontmatter_text) if frontmatter_text is not None else None

    md = create_markdown()
    env: dict[str, Any] = {"image_sizes": dict(image_sizes or {})}
    tokens = md.parse(body, env)
    raw_html = md.renderer.render(tokens, md.options, env)
    words = _count_words(tokens)

    return RenderResult(
        html=sanitize_html(raw_html),
        frontmatter=frontmatter,
        toc=tuple(env.get("toc", ())),
        citations=_citations(tokens, md, env),
        diagrams=tuple(env.get("diagrams", ())),
        word_count=words,
        reading_time_minutes=max(1, math.ceil(words / WORDS_PER_MINUTE)) if words else 0,
        unsanitized_html=raw_html,
    )


@lru_cache(maxsize=2)
def create_markdown(*, join_text: bool = True) -> MarkdownIt:
    """The dialect's markdown-it-py instance (reentrant; ``env`` is per call).

    ``join_text=False`` keeps escapes and entities as separate ``text_special``
    tokens (the linter needs to know what was escaped); rendering needs the default.
    """
    md = MarkdownIt("commonmark", {"html": False, "linkify": False, "typographer": False})
    if not join_text:
        md.core.ruler.disable("text_join")
    md.enable("table")
    md.use(tasklists_plugin)
    md.use(footnote_plugin, inline=False)
    for kind in CALLOUT_TYPES:
        md.use(container_plugin, name=kind, validate=_exact_type(kind), render=_render_callout)
    md.core.ruler.push("heading_ids", _heading_ids)

    default_image = md.renderer.rules["image"]  # type: ignore[attr-defined]

    def render_image(
        self: Any, tokens: Sequence[Token], idx: int, options: OptionsDict, env: EnvType
    ) -> str:
        token = tokens[idx]
        size = env.get("image_sizes", {}).get(str(token.attrGet("src") or ""))
        if size is not None:
            token.attrSet("width", str(int(size[0])))
            token.attrSet("height", str(int(size[1])))
        return str(default_image(tokens, idx, options, env))

    md.add_render_rule("image", render_image)
    md.add_render_rule("fence", _render_fence)
    md.add_render_rule("code_block", _render_code_block)
    return md


def _exact_type(kind: str) -> Any:
    def validate(params: str, *_: Any) -> bool:
        return params.strip() == kind

    return validate


def _render_callout(
    self: Any, tokens: Sequence[Token], idx: int, options: OptionsDict, env: EnvType
) -> str:
    token = tokens[idx]
    if token.nesting == 1:
        kind = token.info.strip()
        return f'<aside class="callout callout-{escapeHtml(kind)}">\n'
    return "</aside>\n"


_FORMATTER = HtmlFormatter(nowrap=True)


def _code_html(code: str, language: str) -> str:
    lexer = None
    if language:
        try:
            lexer = get_lexer_by_name(language, stripnl=False, ensurenl=False)
        except ClassNotFound:
            lexer = None
    body = highlight(code, lexer, _FORMATTER) if lexer is not None else escapeHtml(code)
    class_attr = (
        f' class="language-{escapeHtml(language)}"' if LANGUAGE_RE.fullmatch(language) else ""
    )
    return f'<pre class="highlight"><code{class_attr}>{body}</code></pre>\n'


def _render_fence(
    self: Any, tokens: Sequence[Token], idx: int, options: OptionsDict, env: EnvType
) -> str:
    token = tokens[idx]
    language = info_language(unescapeAll(token.info))
    if language == MERMAID_LANGUAGE:
        digest = hashlib.sha256(token.content.encode("utf-8")).hexdigest()
        env.setdefault("diagrams", []).append(Diagram(digest, token.content))
        return (
            f'<figure class="mermaid" data-diagram="{digest}">'
            f'<pre class="mermaid-source"><code>{escapeHtml(token.content)}</code></pre>'
            "</figure>\n"
        )
    return _code_html(token.content, language)


def _render_code_block(
    self: Any, tokens: Sequence[Token], idx: int, options: OptionsDict, env: EnvType
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


def _heading_ids(state: StateCore) -> None:
    """Give h2/h3 an id (inside the body, not in footnotes) and collect the TOC."""
    slugger = Slugger()
    toc: list[TocEntry] = []
    in_footnotes = False
    for index, token in enumerate(state.tokens):
        if token.type == "footnote_block_open":
            in_footnotes = True
        elif token.type == "footnote_block_close":
            in_footnotes = False
        if token.type != "heading_open" or in_footnotes:
            continue
        level = int(token.tag[1])
        if level not in TOC_HEADING_LEVELS:
            continue
        text = _inline_text(state.tokens[index + 1].children).strip()
        heading_id = slugger.slug(text)
        token.attrSet("id", heading_id)
        toc.append(TocEntry(level, heading_id, text))
    state.env["toc"] = toc


def _citations(tokens: Sequence[Token], md: MarkdownIt, env: EnvType) -> tuple[Citation, ...]:
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
            if inner.type == "inline":
                text_parts.append(_inline_text(inner.children))
                for child in inner.children or ():
                    if child.type == "link_open" and url is None:
                        href = str(child.attrGet("href") or "")
                        # Metadata skips nh3, so apply the profile's href rule here
                        # (markdown-it alone lets http:, //host and relative links through).
                        url = href if _allowed_href(href) else None
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
