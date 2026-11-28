"""Dialect conformance linter (the "dialect conformance" part of gate 5).

Reads the Markdown the way the renderer does (markdown-it-py tokens) and
reports what the dialect does not allow. Findings carry a stable ``code``,
a severity and a 1-based line number (``None`` for document-level findings).
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

from markdown_it import MarkdownIt
from markdown_it.token import Token

from .dialect import (
    BODY_HEADING_LEVELS,
    CITATION_ID_MAX_LENGTH,
    CITATION_ID_RE,
    LANGUAGE_RE,
    MAX_SOURCE_BYTES,
    MERMAID_LANGUAGE,
    info_language,
    normalize_source,
)
from .frontmatter import FrontmatterError, parse_frontmatter, split_frontmatter
from .renderer import create_markdown
from .sanitizer import _allowed_href, _allowed_src

Severity = Literal["error", "warning"]


@dataclass(frozen=True, slots=True)
class Finding:
    code: str
    severity: Severity
    line: int | None
    message: str


_LITERAL_CITATION_RE = re.compile(r"\[\^[^\] \n]+\]")
_CALLOUT_LOOKALIKE_RE = re.compile(r"^:{3,}")
_FORBIDDEN_CHARS_RE = re.compile("[\u2028\u2029\ue010-\ue03f]")
_CONTROL_RE = re.compile("[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u009f]")


@lru_cache(maxsize=1)
def _html_probe() -> MarkdownIt:
    """Same block structure, but with HTML recognition on, to find raw HTML."""
    return MarkdownIt("commonmark", {"html": True}).enable("table")


def _accept_every_link(url: str) -> bool:
    return True


@lru_cache(maxsize=1)
def _unvalidated_links_probe() -> MarkdownIt:
    """Renderer configuration that also keeps the links markdown-it refuses.

    markdown-it drops ``javascript:``, ``vbscript:``, ``file:`` and most
    ``data:`` targets before any rule sees them, so ``[x](javascript:...)``
    renders as literal text and the link checks never ran.
    """
    md = create_markdown.__wrapped__(join_text=True)  # a fresh instance, not the cached renderer
    md.validateLink = _accept_every_link  # type: ignore[method-assign]
    return md


@lru_cache(maxsize=1)
def _definitions_probe() -> MarkdownIt:
    """Renderer configuration without footnote_tail, so definitions stay in place."""
    md = MarkdownIt("commonmark", {"html": False})
    md.enable("table")
    from mdit_py_plugins.footnote import footnote_plugin

    footnote_plugin(md, inline=False, move_to_end=False)
    return md


def lint_article(
    source: str, *, require_frontmatter: bool = True, source_ids: Collection[str] | None = None
) -> list[Finding]:
    """Lint an article. ``source_ids``: brief source ids that citations must bind to."""
    if len(source.encode("utf-8")) > MAX_SOURCE_BYTES:
        # The renderer refuses these too; parsing them four times here would only burn
        # seconds of CPU on every save (reviewer measurement: up to ~19 s at the cap).
        return [
            Finding(
                "source-too-large",
                "error",
                None,
                f"the article is larger than {MAX_SOURCE_BYTES} bytes",
            )
        ]

    findings: list[Finding] = []
    text = normalize_source(source)

    try:
        frontmatter_text, body = split_frontmatter(text)
        if frontmatter_text is None:
            if require_frontmatter:
                findings.append(
                    Finding("frontmatter-missing", "error", 1, "the article has no frontmatter")
                )
        else:
            parse_frontmatter(frontmatter_text)
    except FrontmatterError as error:
        findings.extend(
            Finding("frontmatter-invalid", "error", 1, f"{issue.path}: {issue.message}")
            for issue in error.issues
        )
        return findings

    offset = text.count("\n", 0, len(text) - len(body))
    tokens = create_markdown(join_text=False).parse(body, {})

    _check_characters(body, offset, findings)
    _check_raw_html(body, offset, findings)
    _check_blocks(tokens, offset, findings)
    _check_inline(tokens, offset, findings)
    _check_refused_links(body, offset, findings)
    _check_citations(body, tokens, offset, source_ids, findings)
    return sorted(findings, key=lambda f: (f.line or 0, f.code))


def _line(token: Token, offset: int) -> int | None:
    return token.map[0] + 1 + offset if token.map else None


def _check_characters(body: str, offset: int, findings: list[Finding]) -> None:
    if body.lstrip("\n").startswith("---"):
        findings.append(
            Finding("thematic-break-first", "error", offset + 1, "the body must not start with ---")
        )
    for number, line in enumerate(body.split("\n"), start=offset + 1):
        if _FORBIDDEN_CHARS_RE.search(line):
            findings.append(
                Finding(
                    "forbidden-character",
                    "error",
                    number,
                    "U+2028/U+2029 or a private-use placeholder",
                )
            )
        if _CONTROL_RE.search(line):
            findings.append(Finding("control-character", "error", number, "control character"))


def _check_raw_html(body: str, offset: int, findings: list[Finding]) -> None:
    for token in _html_probe().parse(body):
        if token.type == "html_block":
            findings.append(Finding("raw-html", "error", _line(token, offset), "raw HTML block"))
        elif token.type == "inline" and any(
            child.type == "html_inline" for child in token.children or ()
        ):
            findings.append(Finding("raw-html", "error", _line(token, offset), "inline HTML"))


def _check_blocks(tokens: Sequence[Token], offset: int, findings: list[Finding]) -> None:
    container_depth = 0
    list_depth = 0
    quote_depth = 0
    for index, token in enumerate(tokens):
        line = _line(token, offset)
        if token.type.startswith("container_") and token.type.endswith("_open"):
            if container_depth or list_depth or quote_depth:
                findings.append(
                    Finding("callout-nested", "error", line, "callouts are top-level blocks only")
                )
            container_depth += 1
        elif token.type.startswith("container_") and token.type.endswith("_close"):
            container_depth -= 1
        elif token.type in ("bullet_list_open", "ordered_list_open"):
            list_depth += 1
            _check_list(tokens, index, offset, findings)
        elif token.type in ("bullet_list_close", "ordered_list_close"):
            list_depth -= 1
        elif token.type == "blockquote_open":
            quote_depth += 1
        elif token.type == "blockquote_close":
            quote_depth -= 1
        elif token.type == "heading_open":
            level = int(token.tag[1])
            if level not in BODY_HEADING_LEVELS:
                findings.append(
                    Finding(
                        "heading-level",
                        "error",
                        line,
                        f"h{level} is not allowed in the body (use h2-h4)",
                    )
                )
            if container_depth or list_depth or quote_depth:
                findings.append(
                    Finding("heading-nested", "error", line, "headings must be top-level")
                )
            if token.markup in ("=", "-"):
                findings.append(Finding("setext-heading", "warning", line, "use ATX (##) headings"))
            if not (tokens[index + 1].children or []):
                findings.append(Finding("heading-empty", "error", line, "empty heading"))
        elif token.type == "fence":
            language = info_language(token.info)
            if token.info.strip() and (
                token.info.strip() != language or not LANGUAGE_RE.fullmatch(language)
            ):
                findings.append(
                    Finding(
                        "code-info",
                        "warning",
                        line,
                        "the info string must be one lowercase language name",
                    )
                )
            if language == MERMAID_LANGUAGE and not token.content.strip():
                findings.append(Finding("mermaid-empty", "error", line, "empty mermaid diagram"))
        elif token.type == "code_block":
            findings.append(Finding("indented-code", "warning", line, "use a fenced code block"))
        elif token.type == "paragraph_open":
            inline = tokens[index + 1]
            if _CALLOUT_LOOKALIKE_RE.match(inline.content):
                findings.append(
                    Finding(
                        "callout-invalid",
                        "error",
                        line,
                        "':::' line that is not a valid callout (note|tip|warning, no title)",
                    )
                )


def _check_list(tokens: Sequence[Token], index: int, offset: int, findings: list[Finding]) -> None:
    """Direct items of the list at ``index``: all tasks or none."""
    level = tokens[index].level
    kinds: set[bool] = set()
    for token in tokens[index + 1 :]:
        if token.level == level and token.type in ("bullet_list_close", "ordered_list_close"):
            break
        if token.type == "list_item_open" and token.level == level + 1:
            kinds.add("task-list-item" in str(token.attrGet("class") or ""))
    if len(kinds) > 1:
        findings.append(
            Finding(
                "mixed-task-list",
                "error",
                _line(tokens[index], offset),
                "a list must be all tasks or no tasks",
            )
        )


def _check_inline(tokens: Sequence[Token], offset: int, findings: list[Finding]) -> None:
    in_footnotes = False
    for token in tokens:
        if token.type == "footnote_block_open":
            in_footnotes = True
        elif token.type == "footnote_block_close":
            in_footnotes = False
        if token.type != "inline":
            continue
        line = _line(token, offset)
        children = token.children or []
        meaningful = [c for c in children if not (c.type == "text" and not c.content.strip())]
        link_depth = 0
        for position, child in enumerate(children):
            following = children[position + 1] if position + 1 < len(children) else None
            if (
                child.type == "footnote_ref"
                and following is not None
                and following.type == "text"
                and following.content.startswith("](")
            ):
                # "[text [^ref]](url)": markdown-it drops the link, TipTap keeps it.
                findings.append(
                    Finding("citation-in-link", "error", line, "citation inside link text")
                )
            elif child.type == "link_open":
                link_depth += 1
                href = str(child.attrGet("href") or "")
                if not _allowed_href(href):
                    findings.append(
                        Finding(
                            "link-target",
                            "error",
                            line,
                            f"link target not allowed: {href!r} (https, mailto, #, /)",
                        )
                    )
            elif child.type == "link_close":
                link_depth -= 1
            elif child.type == "footnote_ref" and link_depth:
                findings.append(
                    Finding("citation-in-link", "error", line, "citation inside link text")
                )
            elif child.type == "image":
                src = str(child.attrGet("src") or "")
                if not _allowed_src(src):
                    findings.append(
                        Finding(
                            "image-src",
                            "error",
                            line,
                            f"image source not allowed: {src!r} (https or /)",
                        )
                    )
                if not "".join(c.content for c in child.children or ()).strip():
                    findings.append(Finding("image-alt", "error", line, "image without alt text"))
                if len(meaningful) > 1 and not in_footnotes:
                    findings.append(
                        Finding(
                            "inline-image", "error", line, "an image must be alone in its paragraph"
                        )
                    )
            elif child.type == "text" and (literal := _LITERAL_CITATION_RE.search(child.content)):
                findings.append(
                    Finding(
                        "citation-undefined", "error", line, f"no definition for {literal.group(0)}"
                    )
                )


def _check_refused_links(body: str, offset: int, findings: list[Finding]) -> None:
    """Links and images whose target markdown-it refuses (they render as literal text)."""
    renderer = create_markdown()
    for token in _unvalidated_links_probe().parse(body, {}):
        if token.type != "inline":
            continue
        for child in token.children or ():
            if child.type == "link_open":
                href = str(child.attrGet("href") or "")
                if not renderer.validateLink(href):
                    findings.append(
                        Finding(
                            "link-target",
                            "error",
                            _line(token, offset),
                            f"link target not allowed: {href!r} (renders as literal text)",
                        )
                    )
            elif child.type == "image":
                src = str(child.attrGet("src") or "")
                if not renderer.validateLink(src):
                    findings.append(
                        Finding(
                            "image-src",
                            "error",
                            _line(token, offset),
                            f"image source not allowed: {src!r} (renders as literal text)",
                        )
                    )


def _check_citations(
    body: str,
    tokens: Sequence[Token],
    offset: int,
    source_ids: Collection[str] | None,
    findings: list[Finding],
) -> None:
    referenced = {
        str(child.meta.get("label"))
        for token in tokens
        if token.type == "inline"
        for child in token.children or ()
        if child.type == "footnote_ref"
    }

    definition_tokens = _definitions_probe().parse(body, {})
    definitions = [t for t in definition_tokens if t.type == "footnote_reference_open"]
    counts = Counter(str(t.meta["label"]) for t in definitions)
    last_block_line = max(
        (t.map[1] for t in tokens if t.map and t.level == 0 and not t.type.startswith("footnote")),
        default=0,
    )

    for token in definitions:
        label = str(token.meta["label"])
        line = _line(token, offset)
        if counts[label] > 1:
            findings.append(
                Finding(
                    "citation-duplicate", "error", line, f"[^{label}] is defined more than once"
                )
            )
        if label not in referenced:
            findings.append(
                Finding("citation-unused", "warning", line, f"[^{label}] is never cited")
            )
        if not CITATION_ID_RE.fullmatch(label) or len(label) > CITATION_ID_MAX_LENGTH:
            findings.append(
                Finding("citation-id", "error", line, f"[^{label}] is not a source id ([a-z0-9-])")
            )
        elif source_ids is not None and label not in source_ids:
            findings.append(
                Finding(
                    "citation-unknown-source",
                    "error",
                    line,
                    f"[^{label}] is not a source in the brief",
                )
            )
        if token.map and token.map[0] < last_block_line:
            findings.append(
                Finding("citation-placement", "warning", line, "definitions belong at the end")
            )

    current: list[Token] | None = None
    for token in definition_tokens:
        if token.type == "footnote_reference_open":
            current = []
        elif token.type == "footnote_reference_close" and current is not None:
            blocks = [t for t in current if t.nesting == 1 and t.level == token.level + 1]
            if len(blocks) > 1 or any(t.type != "paragraph_open" for t in blocks):
                findings.append(
                    Finding(
                        "citation-definition-blocks",
                        "error",
                        None,
                        "a definition is one paragraph of text",
                    )
                )
            current = None
        elif current is not None:
            current.append(token)
