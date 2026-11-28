"""Article frontmatter: split, parse with a strict schema, canonical serialization.

Twin of ``web/src/dialect/frontmatter.ts``. Parsing uses ``yaml.BaseLoader``
(every scalar is a string, like the YAML failsafe schema used on the web side)
and converts each key explicitly, so ``no``, ``2024-01-01`` or ``1.0`` never
change type between the editor and the server.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Final

import yaml
from yaml.constructor import ConstructorError
from yaml.nodes import MappingNode

from .dialect import SLUG_RE

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

_FENCE = "---"
_CLOSING_FENCE_RE = re.compile(r"^---[ \t]*$", re.MULTILINE)
_POSITIVE_INT_RE = re.compile(r"^[1-9][0-9]{0,3}$")
_NOT_PLAIN_TEXT_RE = re.compile(
    "[\u0000-\u001f\u007f-\u009f\u2028\u2029\ufeff\ufffe\uffff\ud800-\udfff]"
)


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
    path: str
    message: str


class FrontmatterError(ValueError):
    def __init__(self, issues: list[FrontmatterIssue]) -> None:
        self.issues = issues
        super().__init__(
            "Invalid frontmatter: " + "; ".join(f"{i.path}: {i.message}" for i in issues)
        )


def split_frontmatter(source: str) -> tuple[str | None, str]:
    """Split ``---`` fenced YAML from the body. ``source`` must use ``\\n`` line endings."""
    if not source.startswith(_FENCE + "\n"):
        return None, source

    rest = source[len(_FENCE) + 1 :]
    match = _CLOSING_FENCE_RE.search(rest)
    if match is None:
        raise FrontmatterError([FrontmatterIssue("(frontmatter)", "missing closing --- fence")])

    # Leading blank lines are insignificant in Markdown; the canonical form has one.
    return rest[: match.start()], rest[match.end() :].lstrip("\n")


class _StrictLoader(yaml.BaseLoader):
    """BaseLoader (all scalars are strings) that rejects duplicate keys and aliases."""

    def construct_mapping(self, node: MappingNode, deep: bool = False) -> dict[Any, Any]:
        seen: set[Any] = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=deep)
            if key in seen:
                raise ConstructorError(None, None, f"duplicate key {key!r}", key_node.start_mark)
            seen.add(key)
        return super().construct_mapping(node, deep=deep)

    def compose_node(self, parent: Any, index: Any) -> Any:
        if self.check_event(yaml.AliasEvent):
            event = self.peek_event()  # type: ignore[no-untyped-call]
            raise yaml.composer.ComposerError(
                None, None, "aliases are not allowed", event.start_mark
            )
        return super().compose_node(parent, index)


def parse_frontmatter(text: str) -> Frontmatter:
    """Parse and validate frontmatter YAML; raises FrontmatterError listing every problem."""
    if len(text.encode("utf-8")) > MAX_FRONTMATTER_BYTES:
        raise FrontmatterError(
            [FrontmatterIssue("(frontmatter)", f"larger than {MAX_FRONTMATTER_BYTES} bytes")]
        )
    try:
        data = yaml.load(text, Loader=_StrictLoader)  # noqa: S506 - BaseLoader subclass: strings only
    except yaml.YAMLError as error:
        first_line = str(error).splitlines()[0] if str(error) else type(error).__name__
        raise FrontmatterError(
            [FrontmatterIssue("(frontmatter)", f"YAML error: {first_line}")]
        ) from error
    except RecursionError as error:
        # PyYAML composes recursively: ~3 KB of "[[[[..." exceeds the stack well
        # under the size cap. Report it as invalid frontmatter instead of crashing.
        raise FrontmatterError(
            [FrontmatterIssue("(frontmatter)", "YAML error: nested too deeply")]
        ) from error
    return validate_frontmatter(data)


def validate_frontmatter(data: object) -> Frontmatter:
    if not isinstance(data, dict):
        raise FrontmatterError([FrontmatterIssue("(frontmatter)", "must be a mapping")])

    issues: list[FrontmatterIssue] = []
    issues.extend(
        FrontmatterIssue(str(k), "unknown key") for k in data if k not in FRONTMATTER_KEYS
    )
    issues.extend(FrontmatterIssue(k, "required") for k in REQUIRED_KEYS if k not in data)

    title = _plain_text(data.get("title"), "title", MAX_TITLE, issues)
    slug = _slug(data.get("slug"), "slug", MAX_SLUG, issues)
    excerpt = _plain_text(data.get("excerpt"), "excerpt", MAX_EXCERPT, issues)
    pillar = _slug(data.get("pillar"), "pillar", MAX_SLUG, issues)
    format_ = _slug(data.get("format"), "format", MAX_SLUG, issues)
    tags = _tags(data.get("tags"), issues)
    series = _series(data["series"], issues) if "series" in data else None

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
    # JS `trim` semantics (the web twin) differ from str.strip only for control
    # characters, which are rejected below anyway.
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
    if len(value) > MAX_TAGS:
        issues.append(FrontmatterIssue("tags", f"must have at most {MAX_TAGS} entries"))
    seen: set[str] = set()
    for index, tag in enumerate(value):
        tag_value = _slug(tag, f"tags[{index}]", MAX_TAG, issues)
        if tag_value in seen:
            issues.append(FrontmatterIssue(f"tags[{index}]", "duplicate tag"))
        seen.add(tag_value)
    return tuple(tag for tag in value if isinstance(tag, str))


def _series(value: object, issues: list[FrontmatterIssue]) -> Series | None:
    if not isinstance(value, dict):
        issues.append(FrontmatterIssue("series", "must be a mapping"))
        return None
    issues.extend(
        FrontmatterIssue(f"series.{k}", "unknown key") for k in value if k not in SERIES_KEYS
    )
    issues.extend(
        FrontmatterIssue(f"series.{k}", "required")
        for k in ("id", "title", "part")
        if k not in value
    )
    series_id = _slug(value.get("id"), "series.id", MAX_SLUG, issues)
    title = _plain_text(value.get("title"), "series.title", MAX_SERIES_TITLE, issues)
    part = _positive_int(value.get("part"), "series.part", issues)
    total = _positive_int(value.get("total"), "series.total", issues) if "total" in value else None
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
