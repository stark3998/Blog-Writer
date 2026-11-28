"""Article frontmatter: split, parse with a strict schema, canonical serialization.

Twin of the editor's frontmatter module (Phase 0 spike, ``web/src/dialect/frontmatter.ts``).
Parsing reads the YAML node graph with PyYAML's ``BaseLoader`` resolver (every scalar is a
string, like the YAML *failsafe* schema on the web side) and converts each key explicitly, so
``no``, ``2024-01-01`` or ``1.0`` never change type between the editor and the server, and
explicit tags such as ``!!binary`` are plain strings on both sides. Duplicate keys, aliases and
nesting too deep to parse are errors.

:func:`dump_frontmatter` writes the canonical form: fixed key order, free text double-quoted with
JSON-style escapes, slugs bare, tags as a flow list, ``series`` as a block mapping and ``total``
omitted when unknown. Its output is byte-identical to the editor's for every corpus file.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final, cast

import yaml
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode

from .dialect import SLUG_RE

__all__ = [
    "FRONTMATTER_KEYS",
    "MAX_EXCERPT",
    "MAX_FRONTMATTER_BYTES",
    "MAX_SERIES_TITLE",
    "MAX_SLUG",
    "MAX_TAG",
    "MAX_TAGS",
    "MAX_TITLE",
    "REQUIRED_KEYS",
    "SERIES_KEYS",
    "Frontmatter",
    "FrontmatterError",
    "FrontmatterIssue",
    "Series",
    "dump_frontmatter",
    "parse_frontmatter",
    "quote_yaml_string",
    "split_frontmatter",
    "validate_frontmatter",
]

FRONTMATTER_KEYS: Final = ("title", "slug", "excerpt", "pillar", "format", "tags", "series")
"""Canonical key order, and the complete set of allowed keys."""
REQUIRED_KEYS: Final = ("title", "slug", "excerpt", "pillar", "format", "tags")
SERIES_KEYS: Final = ("id", "title", "part", "total")

MAX_FRONTMATTER_BYTES: Final = 8 * 1024
MAX_TITLE: Final = 150
MAX_EXCERPT: Final = 400
MAX_SLUG: Final = 80
MAX_TAG: Final = 40
MAX_TAGS: Final = 10
MAX_SERIES_TITLE: Final = 150

_FENCE: Final = "---"
_CLOSING_FENCE_RE: Final = re.compile(r"^---[ \t]*$", re.MULTILINE)
_POSITIVE_INT_RE: Final = re.compile(r"[1-9][0-9]{0,3}")
_NOT_PLAIN_TEXT_RE: Final = re.compile(
    r"[\x00-\x1f\x7f-\x9f\u2028\u2029\ufeff\ufffe\uffff\ud800-\udfff]"
)

type _Yaml = str | list[_Yaml] | dict[str, _Yaml] | None


@dataclass(frozen=True, slots=True)
class Series:
    id: str
    title: str
    part: int
    total: int | None = None


@dataclass(frozen=True, slots=True)
class Frontmatter:
    title: str
    slug: str
    excerpt: str
    pillar: str
    format: str
    tags: tuple[str, ...]
    series: Series | None = None


@dataclass(frozen=True, slots=True)
class FrontmatterIssue:
    """One problem. ``path`` is a key path (``tags[1]``, ``series.part``) or ``(frontmatter)``."""

    path: str
    message: str


class FrontmatterError(ValueError):
    """The frontmatter is missing its closing fence, is not valid YAML, or breaks the schema."""

    def __init__(self, issues: list[FrontmatterIssue]) -> None:
        self.issues = tuple(issues)
        super().__init__(
            "Invalid frontmatter: " + "; ".join(f"{i.path}: {i.message}" for i in issues)
        )


def split_frontmatter(source: str) -> tuple[str | None, str]:
    """Split ``---`` fenced YAML from the body. ``source`` must use ``\\n`` line endings.

    Returns ``(yaml_text, body)``; ``yaml_text`` is ``None`` when there is no frontmatter. Blank
    lines at the start of the body are dropped (the canonical form has exactly one).
    """
    if not source.startswith(_FENCE + "\n"):
        return None, source

    rest = source[len(_FENCE) + 1 :]
    match = _CLOSING_FENCE_RE.search(rest)
    if match is None:
        raise FrontmatterError([FrontmatterIssue("(frontmatter)", "missing closing --- fence")])
    return rest[: match.start()], rest[match.end() :].lstrip("\n")


def parse_frontmatter(text: str) -> Frontmatter:
    """Parse and validate frontmatter YAML; raises :class:`FrontmatterError` listing every
    problem."""
    if len(text.encode("utf-8", errors="surrogatepass")) > MAX_FRONTMATTER_BYTES:
        raise FrontmatterError(
            [FrontmatterIssue("(frontmatter)", f"larger than {MAX_FRONTMATTER_BYTES} bytes")]
        )
    try:
        node = yaml.compose(text, Loader=yaml.BaseLoader)
        data = _construct(node, set()) if node is not None else None
    except yaml.YAMLError as error:
        first_line = str(error).splitlines()[0] if str(error) else type(error).__name__
        raise FrontmatterError(
            [FrontmatterIssue("(frontmatter)", f"YAML error: {first_line}")]
        ) from error
    except RecursionError as error:
        # PyYAML composes recursively: ~3 KB of "[[[[..." exceeds the stack well under the size
        # cap. Report it as invalid frontmatter instead of crashing.
        raise FrontmatterError(
            [FrontmatterIssue("(frontmatter)", "YAML error: nested too deeply")]
        ) from error
    return validate_frontmatter(data)


class _YamlShapeError(yaml.YAMLError):
    pass


def _construct(node: Node, seen: set[int]) -> _Yaml:
    """The plain value of a composed node: strings, lists and string-keyed dicts.

    The composer resolves an alias to its anchored node, so an alias shows up as a node visited
    twice (recursive aliases included); that is refused, like duplicate keys.
    """
    if id(node) in seen:
        raise _YamlShapeError("aliases are not allowed")
    seen.add(id(node))
    value = cast("object", node.value)
    if isinstance(node, ScalarNode):
        return cast("str", value)
    if isinstance(node, SequenceNode):
        return [_construct(item, seen) for item in cast("list[Node]", value)]
    if isinstance(node, MappingNode):
        result: dict[str, _Yaml] = {}
        for key_node, value_node in cast("list[tuple[Node, Node]]", value):
            key = _construct(key_node, seen)
            if not isinstance(key, str):
                raise _YamlShapeError("mapping keys must be strings")
            if key in result:
                raise _YamlShapeError(f"duplicate key {key!r}")
            result[key] = _construct(value_node, seen)
        return result
    raise _YamlShapeError(f"unsupported node {type(node).__name__}")


def validate_frontmatter(data: object) -> Frontmatter:
    """Check parsed frontmatter against the dialect's schema."""
    if not isinstance(data, dict):
        raise FrontmatterError([FrontmatterIssue("(frontmatter)", "must be a mapping")])
    mapping = cast("dict[str, object]", data)

    issues: list[FrontmatterIssue] = []
    issues.extend(
        FrontmatterIssue(str(key), "unknown key") for key in mapping if key not in FRONTMATTER_KEYS
    )
    issues.extend(FrontmatterIssue(key, "required") for key in REQUIRED_KEYS if key not in mapping)

    title = _plain_text(mapping.get("title"), "title", MAX_TITLE, issues)
    slug = _slug(mapping.get("slug"), "slug", MAX_SLUG, issues)
    excerpt = _plain_text(mapping.get("excerpt"), "excerpt", MAX_EXCERPT, issues)
    pillar = _slug(mapping.get("pillar"), "pillar", MAX_SLUG, issues)
    format_ = _slug(mapping.get("format"), "format", MAX_SLUG, issues)
    tags = _tags(mapping.get("tags"), issues)
    series = _series(mapping["series"], issues) if "series" in mapping else None

    if issues:
        raise FrontmatterError(issues)
    return Frontmatter(title, slug, excerpt, pillar, format_, tags, series)


def dump_frontmatter(fm: Frontmatter) -> str:
    """Canonical YAML including both ``---`` fences and a trailing newline."""
    lines = [
        _FENCE,
        f"title: {quote_yaml_string(fm.title)}",
        f"slug: {fm.slug}",
        f"excerpt: {quote_yaml_string(fm.excerpt)}",
        f"pillar: {fm.pillar}",
        f"format: {fm.format}",
        f"tags: [{', '.join(fm.tags)}]",
    ]
    if fm.series is not None:
        lines += [
            "series:",
            f"  id: {fm.series.id}",
            f"  title: {quote_yaml_string(fm.series.title)}",
            f"  part: {fm.series.part}",
        ]
        if fm.series.total is not None:
            lines.append(f"  total: {fm.series.total}")
    lines.append(_FENCE)
    return "\n".join(lines) + "\n"


def quote_yaml_string(value: str) -> str:
    """Double-quoted YAML scalar; JSON-style escapes, line-break-like characters as ``\\uXXXX``."""
    out = ['"']
    for ch in value:
        cp = ord(ch)
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif ch == "\r":
            out.append("\\r")
        elif _needs_unicode_escape(cp):
            out.append(f"\\u{cp:04X}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def _needs_unicode_escape(cp: int) -> bool:
    return (
        cp < 0x20
        or cp == 0x7F
        or 0x80 <= cp <= 0x9F
        or cp in (0x2028, 0x2029, 0xFEFF, 0xFFFE, 0xFFFF)
        or 0xD800 <= cp <= 0xDFFF
    )


def _plain_text(value: object, path: str, max_length: int, issues: list[FrontmatterIssue]) -> str:
    if not isinstance(value, str):
        if value is not None:
            issues.append(FrontmatterIssue(path, "must be a string"))
        return ""
    # JS `trim` semantics (the web twin) differ from str.strip only for control characters,
    # which are rejected below anyway.
    if value.strip() == "":
        issues.append(FrontmatterIssue(path, "must not be empty"))
    elif value != value.strip():
        issues.append(FrontmatterIssue(path, "must not start or end with whitespace"))
    if "<" in value:
        issues.append(FrontmatterIssue(path, 'must not contain "<" (plain text only)'))
    if _NOT_PLAIN_TEXT_RE.search(value):
        issues.append(FrontmatterIssue(path, "must not contain control characters or line breaks"))
    if len(value) > max_length:
        issues.append(FrontmatterIssue(path, f"must be at most {max_length} characters"))
    return value


def _slug(value: object, path: str, max_length: int, issues: list[FrontmatterIssue]) -> str:
    if not isinstance(value, str):
        if value is not None:
            issues.append(FrontmatterIssue(path, "must be a string"))
        return ""
    if not SLUG_RE.fullmatch(value):
        issues.append(FrontmatterIssue(path, "must be lowercase kebab-case ([a-z0-9-])"))
    elif len(value) > max_length:
        issues.append(FrontmatterIssue(path, f"must be at most {max_length} characters"))
    return value


def _tags(value: object, issues: list[FrontmatterIssue]) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        issues.append(FrontmatterIssue("tags", "must be a list"))
        return ()
    items = cast("list[object]", value)
    if len(items) > MAX_TAGS:
        issues.append(FrontmatterIssue("tags", f"must have at most {MAX_TAGS} entries"))
    seen: set[str] = set()
    for index, tag in enumerate(items):
        tag_value = _slug(tag, f"tags[{index}]", MAX_TAG, issues)
        if tag_value in seen:
            issues.append(FrontmatterIssue(f"tags[{index}]", "duplicate tag"))
        seen.add(tag_value)
    return tuple(tag for tag in items if isinstance(tag, str))


def _series(value: object, issues: list[FrontmatterIssue]) -> Series | None:
    if not isinstance(value, dict):
        issues.append(FrontmatterIssue("series", "must be a mapping"))
        return None
    mapping = cast("dict[str, object]", value)
    issues.extend(
        FrontmatterIssue(f"series.{key}", "unknown key")
        for key in mapping
        if key not in SERIES_KEYS
    )
    issues.extend(
        FrontmatterIssue(f"series.{key}", "required")
        for key in ("id", "title", "part")
        if key not in mapping
    )
    series_id = _slug(mapping.get("id"), "series.id", MAX_SLUG, issues)
    title = _plain_text(mapping.get("title"), "series.title", MAX_SERIES_TITLE, issues)
    part = _positive_int(mapping.get("part"), "series.part", issues)
    total = (
        _positive_int(mapping.get("total"), "series.total", issues) if "total" in mapping else None
    )
    if part is not None and total is not None and total < part:
        issues.append(
            FrontmatterIssue("series.total", "must be greater than or equal to series.part")
        )
    return Series(series_id, title, part or 1, total)


def _positive_int(value: object, path: str, issues: list[FrontmatterIssue]) -> int | None:
    if not isinstance(value, str) or not _POSITIVE_INT_RE.fullmatch(value):
        if value is not None:
            issues.append(FrontmatterIssue(path, "must be a positive integer (1-9999)"))
        return None
    return int(value)
