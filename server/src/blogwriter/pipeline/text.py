"""Pure text helpers of the pipeline: cleaning model drafts, binding citations to the brief,
normalizing a body the way it ships, and small string utilities. No I/O.

**Citations come from the brief only.** The drafter writes ``[^src-id]`` references and nothing
else; :func:`bind_citations` drops every definition the model wrote, drops references to ids the
brief does not have (a source the model invented never reaches the article) and appends the
canonical definition of each cited brief source (``[^id]: Publisher. *Title*. [domain](url)``).
So every URL in a draft's footnotes is a URL research found and the brief kept.

The normalizer is the publish package's (:func:`blogwriter.publish.normalize.normalize_markdown`)
run on the body behind a fixed, valid frontmatter head, exactly how the body ships behind
``compose_document``: a body that opens with a thematic break stays one instead of reading as
frontmatter. CPU-bound helpers (:func:`normalize_body`, :func:`mermaid_sources`) belong in
``asyncio.to_thread``.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Collection, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from blogwriter.domain.base import SLUG_PATTERN
from blogwriter.domain.common import Citation
from blogwriter.publish.dialect import MERMAID_LANGUAGE, SourceTooLargeError, info_language
from blogwriter.publish.frontmatter import Frontmatter, dump_frontmatter
from blogwriter.publish.lint import lint_article
from blogwriter.publish.normalize import normalize_markdown
from blogwriter.publish.parser import dialect_parser
from blogwriter.publish.slug import slugify

__all__ = [
    "MAX_DELTA_CHARS",
    "BoundBody",
    "LintIssue",
    "NormalizedBody",
    "bind_citations",
    "chunks",
    "citation_definition",
    "citation_refs",
    "clean_draft",
    "clip",
    "dialect_errors",
    "existing_definitions",
    "known_ids",
    "mermaid_sources",
    "normalize_body",
    "plain_text",
    "slug_of",
    "tag_slugs",
]

MAX_DELTA_CHARS: Final = 4000
"""Token deltas are cut to this size (the broker refuses anything above 8,192 characters)."""

_SLUG_RE: Final = re.compile(SLUG_PATTERN)
_CONTROL_RE: Final = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029\ufeff]")
_FENCE_RE: Final = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_DEFINITION_RE: Final = re.compile(r"^ {0,3}\[\^[^\]\n]+\]:")
_REF_RE: Final = re.compile(r"\[\^([^\]\s]+)\]")
_DEFINITION_LABEL_RE: Final = re.compile(r"^ {0,3}\[\^([^\]\s]+)\]:")
_BACKTICKS_RE: Final = re.compile(r"`+")
_WRAPPED_RE: Final = re.compile(
    r"(`{3,}|~{3,})[ \t]*(?:markdown|md)?[ \t]*\n(?P<body>.*)\n\1[ \t]*", re.DOTALL | re.IGNORECASE
)
_FRONTMATTER_RE: Final = re.compile(
    r"---[ \t]*\n(?:[A-Za-z_][A-Za-z0-9_-]*:[^\n]*\n|[ \t]+[^\n]*\n)+---[ \t]*\n"
)
_H1_RE: Final = re.compile(r"# [^\n]*")
_TAIL_HEADING_RE: Final = re.compile(
    r"^ {0,3}#{2,4}[ \t]+(?:references|sources|further reading|bibliography|citations|notes"
    r"(?: and sources)?|sources and further reading)[ \t]*#*[ \t]*$",
    re.IGNORECASE,
)
_ESCAPED_RE: Final = re.compile(r"([\\`*_\[\]<>&|~#!])")

# The canonical frontmatter the body is normalized behind (a valid placeholder: drafts rarely
# have complete metadata, and the normalizer treats the head and the body independently).
_PLACEHOLDER_HEAD: Final = dump_frontmatter(
    Frontmatter(
        title="Draft", slug="draft", excerpt="Draft", pillar="draft", format="draft", tags=()
    )
)
_HEAD_LINES: Final = _PLACEHOLDER_HEAD.count("\n") + 1


# --------------------------------------------------------------------------------------------
# Small strings
# --------------------------------------------------------------------------------------------


def clip(text: str | None, max_chars: int) -> str:
    """Whitespace collapsed and cut at ``max_chars`` (at a word boundary when one is near)."""
    if not text:
        return ""
    collapsed = " ".join(text.split())
    if len(collapsed) <= max_chars:
        return collapsed
    cut = collapsed[:max_chars]
    boundary = cut.rfind(" ")
    if boundary >= max_chars * 0.6:
        cut = cut[:boundary]
    return cut.rstrip(" ,;:-")


def plain_text(text: str | None, max_chars: int) -> str:
    """Frontmatter-safe plain text: no control characters, line breaks or ``<``, whitespace
    collapsed, clipped. Empty when nothing is left."""
    if not text:
        return ""
    cleaned = _CONTROL_RE.sub(" ", text).replace("<", "").replace(">", "")
    return clip(cleaned, max_chars).strip(" \"'")


def slug_of(text: str | None, max_chars: int = 80) -> str | None:
    """A dialect slug for ``text`` (ASCII kebab-case, at most ``max_chars``), or ``None``."""
    if not text:
        return None
    slug = slugify(text)
    if len(slug) > max_chars:
        cut = slug[:max_chars]
        slug = cut.rsplit("-", 1)[0] if "-" in cut and slug[max_chars] != "-" else cut
        slug = slug.strip("-")
    if slug == "section" and "section" not in text.lower():
        return None  # slugify's placeholder for text without ASCII letters or digits
    return slug if _SLUG_RE.fullmatch(slug) else None


def tag_slugs(tags: Sequence[str], *, limit: int = 6) -> list[str]:
    """Unique tag slugs (at most 40 characters each), in order, at most ``limit``."""
    out: list[str] = []
    for tag in tags:
        slug = slug_of(tag, 40)
        if slug is not None and slug not in out:
            out.append(slug)
        if len(out) >= limit:
            break
    return out


def chunks(text: str, size: int = MAX_DELTA_CHARS) -> Iterator[str]:
    """``text`` in pieces of at most ``size`` characters (token deltas for the broker)."""
    for start in range(0, len(text), size):
        yield text[start : start + size]


# --------------------------------------------------------------------------------------------
# Cleaning a model's draft
# --------------------------------------------------------------------------------------------


def clean_draft(raw: str) -> str:
    """The body part of a drafter's reply: no wrapping code fence, no frontmatter, no level-1
    title, no trailing "References"/"Sources" section; LF line endings and one final newline.

    The dialect has no h1 (the title is the frontmatter's) and the pipeline writes the source
    list itself (:func:`bind_citations`), so these are model habits, never content to keep.
    """
    text = raw.replace("\r\n", "\n").replace("\r", "\n").strip()
    wrapped = _WRAPPED_RE.fullmatch(text)
    if wrapped is not None:
        text = wrapped.group("body").strip()
    head = _FRONTMATTER_RE.match(text + "\n")
    if head is not None:
        text = (text + "\n")[head.end() :].strip()
    first, _, rest = text.partition("\n")
    if _H1_RE.fullmatch(first.strip()):
        text = rest.strip()
    lines = text.split("\n")
    cut = _tail_section_start(lines)
    if cut is not None:
        lines = lines[:cut]
    return "\n".join(lines).strip() + "\n" if any(line.strip() for line in lines) else ""


def _code_lines(lines: Sequence[str]) -> list[bool]:
    """Whether each line is inside (or is a fence of) a fenced code block."""
    inside: list[bool] = []
    fence: str | None = None
    for line in lines:
        match = _FENCE_RE.match(line)
        if fence is None:
            if match is not None:
                fence = match.group(1)
                inside.append(True)
                continue
            inside.append(False)
            continue
        inside.append(True)
        if (
            match is not None
            and match.group(1)[0] == fence[0]
            and len(match.group(1)) >= len(fence)
            and not line.strip()[len(match.group(1)) :].strip()
        ):
            fence = None
    return inside


def _tail_section_start(lines: Sequence[str]) -> int | None:
    """The line where a final "References"-style section starts (outside code), if any."""
    code = _code_lines(lines)
    for index in range(len(lines) - 1, -1, -1):
        if code[index]:
            continue
        if _TAIL_HEADING_RE.match(lines[index]):
            later = [i for i in range(index + 1, len(lines)) if not code[i]]
            if any(re.match(r"^ {0,3}#{2,4}[ \t]", lines[i]) for i in later):
                return None  # another section follows: it is not the tail
            return index
    return None


# --------------------------------------------------------------------------------------------
# Citations
# --------------------------------------------------------------------------------------------


def _inline_code_spans(line: str) -> list[tuple[int, int]]:
    """Inline code spans of one line (a backtick run closed by the next run of equal width)."""
    runs = [(match.start(), match.end()) for match in _BACKTICKS_RE.finditer(line)]
    spans: list[tuple[int, int]] = []
    index = 0
    while index < len(runs):
        start, end = runs[index]
        width = end - start
        for later in range(index + 1, len(runs)):
            if runs[later][1] - runs[later][0] == width:
                spans.append((start, runs[later][1]))
                index = later + 1
                break
        else:
            index += 1
    return spans


def _map_refs(line: str, replace: Callable[[str], str]) -> str:
    spans = _inline_code_spans(line)
    out: list[str] = []
    position = 0
    for match in _REF_RE.finditer(line):
        if any(start <= match.start() < end for start, end in spans):
            continue
        before = line[position : match.start()]
        replacement = replace(match.group(1))
        if not replacement and before.endswith(" "):
            before = before[:-1]
        out.append(before)
        out.append(replacement)
        position = match.end()
    out.append(line[position:])
    return "".join(out)


def citation_refs(body: str) -> list[str]:
    """The ``[^label]`` labels of ``body`` in order of first reference (outside code and
    definitions)."""
    lines = body.split("\n")
    code = _code_lines(lines)
    seen: list[str] = []

    def collect(label: str) -> str:
        if label not in seen:
            seen.append(label)
        return f"[^{label}]"

    for line, in_code in zip(lines, code, strict=True):
        if not in_code and not _DEFINITION_RE.match(line):
            _map_refs(line, collect)
    return seen


def _md_escape(text: str) -> str:
    return _ESCAPED_RE.sub(r"\\\1", " ".join(text.split()))


def citation_definition(citation: Citation) -> str:
    """The canonical footnote line of a brief citation:
    ``[^id]: Publisher. *Title*. [domain](https://...)``."""
    publisher = plain_text(citation.publisher, 200)
    title = plain_text(citation.title, 300) or citation.domain
    url = citation.url.replace("<", "%3C").replace(">", "%3E")
    target = f"<{url}>" if "(" in url or ")" in url else url
    lead = f"{_md_escape(publisher)}. " if publisher else ""
    return f"[^{citation.id}]: {lead}*{_md_escape(title)}*. [{citation.domain}]({target})"


@dataclass(frozen=True, slots=True)
class BoundBody:
    """A body whose citations are bound to the brief."""

    text: str
    cited: tuple[str, ...]
    """Brief citation ids the body cites, in order of first reference."""
    removed: int
    """References to ids the brief does not have (dropped)."""


def existing_definitions(body: str) -> dict[str, str]:
    """The footnote definition lines of ``body`` by label (outside code): an owner's own
    sources survive a revision through :func:`bind_citations`'s ``keep``."""
    lines = body.split("\n")
    code = _code_lines(lines)
    found: dict[str, str] = {}
    for line, in_code in zip(lines, code, strict=True):
        if in_code:
            continue
        match = _DEFINITION_LABEL_RE.match(line)
        if match is not None:
            found[match.group(1)] = line.strip()
    return found


def bind_citations(
    body: str, citations: Sequence[Citation], *, keep: Mapping[str, str] | None = None
) -> BoundBody:
    """Drop the model's footnote definitions and its references to unknown ids, then append
    the canonical definition of every cited brief source (in order of first reference).

    ``keep`` maps labels outside the brief to definition lines to keep as they are (the owner's
    own sources in an article being revised); references to them survive too.
    """
    known = {citation.id: citation for citation in citations}
    kept_lines = dict(keep or {})
    lines = body.split("\n")
    code = _code_lines(lines)
    removed = 0
    cited: list[str] = []

    def replace(label: str) -> str:
        nonlocal removed
        if label not in known and label not in kept_lines:
            removed += 1
            return ""
        if label not in cited:
            cited.append(label)
        return f"[^{label}]"

    kept: list[str] = []
    for line, in_code in zip(lines, code, strict=True):
        if in_code:
            kept.append(line)
        elif not _DEFINITION_RE.match(line):
            kept.append(_map_refs(line, replace))
    text = "\n".join(kept).strip("\n")
    if cited:
        definitions = "\n".join(
            citation_definition(known[label]) if label in known else kept_lines[label]
            for label in cited
        )
        text = f"{text}\n\n{definitions}" if text else definitions
    brief_cited = tuple(label for label in cited if label in known)
    return BoundBody(text=text + "\n" if text else "", cited=brief_cited, removed=removed)


# --------------------------------------------------------------------------------------------
# Normalization and diagrams
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LintIssue:
    """A normalizer error by code and body line (messages may quote the text: never kept)."""

    code: str
    line: int | None


@dataclass(frozen=True, slots=True)
class NormalizedBody:
    text: str
    errors: tuple[LintIssue, ...]


def _body_line(line: int | None, offset: int) -> int | None:
    if line is None:
        return None
    body_line = line - offset
    return body_line if body_line >= 1 else None


def normalize_body(body: str) -> NormalizedBody:
    """The canonical form of an article body and the normalizer errors it hides (the dialect
    errors that the canonical text can no longer show, such as an undefined citation)."""
    if not body.strip():
        return NormalizedBody(text="", errors=())
    composed = f"{_PLACEHOLDER_HEAD}\n{body}"
    try:
        result = normalize_markdown(composed)
    except SourceTooLargeError:
        alone = normalize_markdown(body)
        return NormalizedBody(
            text=alone.text,
            errors=tuple(LintIssue(error.code, error.line) for error in alone.errors),
        )
    errors = tuple(
        LintIssue(error.code, _body_line(error.line, _HEAD_LINES)) for error in result.errors
    )
    if result.text == composed or not result.text.startswith(_PLACEHOLDER_HEAD):
        return NormalizedBody(text=body, errors=errors)
    return NormalizedBody(
        text=result.text[len(_PLACEHOLDER_HEAD) :].removeprefix("\n"), errors=errors
    )


def dialect_errors(body: str, source_ids: Collection[str]) -> tuple[LintIssue, ...]:
    """The publish linter's errors for ``body`` behind a valid frontmatter head, with citations
    bound to ``source_ids`` (what gate 5 will refuse), by code and body line."""
    if not body.strip():
        return ()
    findings = lint_article(f"{_PLACEHOLDER_HEAD}\n{body}", source_ids=source_ids)
    return tuple(
        LintIssue(finding.code, _body_line(finding.line, _HEAD_LINES))
        for finding in findings
        if finding.severity == "error"
    )


def mermaid_sources(body: str, *, limit: int = 10) -> list[str]:
    """The Mermaid fences of ``body`` in document order (at most ``limit``)."""
    sources: list[str] = []
    for token in dialect_parser().parse(body, {}):
        if token.type == "fence" and info_language(token.info) == MERMAID_LANGUAGE:
            if token.content.strip():
                sources.append(token.content)
            if len(sources) >= limit:
                break
    return sources


def known_ids(values: Sequence[str], allowed: Collection[str]) -> list[str]:
    """``values`` that are in ``allowed``, unique, in order."""
    out: list[str] = []
    for value in values:
        if value in allowed and value not in out:
            out.append(value)
    return out
