"""The published-blogs v2 contract: the shared JSON Schema plus the rules it cannot express.

The contract files live in the repository's ``contracts/`` directory, shared with the web app and
the portfolio (see ``contracts/README.md``):

- ``published-blogs.v2.schema.json``: JSON Schema 2020-12 for documents in the Cosmos DB
  containers ``published-blogs`` and ``published-blogs-staging``.
- ``sanitizer-profile.json``: the HTML allowlist that ``htmlContent`` must stay inside.

:func:`validate_published_doc` runs the schema with format checking, then the invariants JSON
Schema cannot express: ``id == slug``, redirect rules, no timestamp in the future, real calendar
dates, ``series.total >= series.part``, unique ``toc``/``citations`` ids, ordered corrections,
asset locations, every site-relative media reference listed in ``assets``, ``toc`` equal to the
h2/h3 headings of ``htmlContent``, ``htmlContent`` a self-contained fragment inside the sanitizer
profile, and the Cosmos DB item size. Writers call it before every upsert and refuse to write on
any error.

Validation is CPU-bound (it parses ``htmlContent``): call it through ``asyncio.to_thread`` from
async code. Error ``code`` and ``path`` are safe to log; ``message`` may quote short document
values, so do not log it.

Patterns in the contract files are ECMA-262 regular expressions (``u`` flag), the regex dialect
JSON Schema specifies and the one Ajv uses in the portfolio. :func:`python_regex` translates the
constructs whose meaning differs in Python's ``re`` (``$``, ``\\s``, ``\\S``, ``.``) and rejects the
engine-dependent ones, so the server and the portfolio accept exactly the same strings.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from functools import cache
from html import unescape as html_unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Final, cast, override

from jsonschema import Draft202012Validator, ValidationError
from jsonschema.exceptions import SchemaError
from jsonschema.protocols import Validator
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = [
    "FUTURE_TOLERANCE",
    "MAX_DOCUMENT_BYTES",
    "MAX_HTML_FINDINGS",
    "MAX_MARKDOWN_BYTES",
    "MEDIA_PREFIX",
    "PROFILE_FILE",
    "PROFILE_VERSION",
    "SCHEMA_FILE",
    "SCHEMA_ID",
    "SCHEMA_VERSION",
    "TOC_HEADING_TAGS",
    "TOC_LEVELS",
    "ContractError",
    "ContractSettings",
    "ContractUnavailableError",
    "HtmlAudit",
    "PublishedContract",
    "SanitizerProfile",
    "audit_html",
    "default_contract",
    "find_contracts_dir",
    "python_regex",
    "validate_published_doc",
]

SCHEMA_FILE: Final = "published-blogs.v2.schema.json"
SCHEMA_ID: Final = "urn:blogwriter:contracts:published-blogs:v2"
SCHEMA_VERSION: Final = 2
PROFILE_FILE: Final = "sanitizer-profile.json"
PROFILE_VERSION: Final = 1

MAX_DOCUMENT_BYTES: Final = 2_000_000
"""Cosmos DB caps an item at 2 MiB of UTF-8 JSON, system properties included; keep headroom."""
MAX_MARKDOWN_BYTES: Final = 512 * 1024
"""The server's cap on article Markdown (``docs/v2/article-dialect.md``)."""
FUTURE_TOLERANCE: Final = timedelta(seconds=60)
"""Clock-skew allowance for "not in the future" checks run on another machine than the writer."""
MAX_HTML_FINDINGS: Final = 20
MEDIA_PREFIX: Final = "/blog-media/"
TOC_HEADING_TAGS: Final = ("h2", "h3")
TOC_LEVELS: Final = (2, 3)
_INVARIANT_DEFS: Final = (
    "utcTimestamp",
    "fullDate",
    "partialDate",
    "slug",
    "assetPath",
    "blobName",
)
"""``$defs`` whose patterns the invariants reuse, so they skip what the schema rejects."""

_PROFILE_KEYS: Final = frozenset(
    {
        "version",
        "tags",
        "attributes",
        "classPatterns",
        "idPatterns",
        "urlPatterns",
        "valuePatterns",
        "styleValues",
        "forcedAttributes",
        "linkRel",
        "dropWithContent",
        "clobberPrefix",
    }
)
_FREE_TEXT_ATTRIBUTES: Final = frozenset({"alt", "title"})
_MEDIA_ATTRIBUTES: Final = {"img": "src", "a": "href"}
_BOOLEAN_ATTRIBUTES: Final = frozenset({"checked", "disabled"})
_VOID_TAGS: Final = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "source",
        "track",
        "wbr",
    }
)
"""HTML void elements: they have no content and no end tag."""
_FRAGMENT_END: Final = "blogwriter-fragment-end"
"""The end tag the audit appends to a fragment, standing in for the page markup that follows it."""
_NAME_LIMIT: Final = 40
_ASCII_WHITESPACE_RE: Final = re.compile(r"[\t\n\f\r ]+")
_NUMERIC_CHARREF_RE: Final = re.compile(r"&#(?:[xX][0-9a-fA-F]+|[0-9]+);?")
# ECMA-262 \s: WhiteSpace and LineTerminator (Python's \s differs: it adds U+001C-U+001F and
# U+0085 and leaves out U+FEFF).
_ECMA_WHITESPACE: Final = (
    r"\t\n\x0b\x0c\r \xa0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"
)
_ECMA_DOT: Final = r"[^\n\r\u2028\u2029]"
_UNTRANSLATABLE_ESCAPES: Final = frozenset("bBdDwWpPk")
_NOT_SCHEMA_KEYWORDS: Final = frozenset({"const", "enum", "examples", "default"})
_KEBAB_RE: Final = re.compile(r"(?<!^)(?=[A-Z])")


class ContractUnavailableError(RuntimeError):
    """The contract files are missing or malformed. Publishing must stop (fail closed)."""


@dataclass(frozen=True, slots=True)
class ContractError:
    """One way a document breaks the contract.

    ``code`` is stable and safe to log; ``path`` is a JSON Pointer into the document (``""`` is the
    document itself); ``message`` explains the problem and may quote short document values.
    """

    code: str
    path: str
    message: str

    @override
    def __str__(self) -> str:
        return f"{self.path or '/'}: {self.message} [{self.code}]"


class ContractSettings(BaseSettings):
    """Where the contract files are. Unset: the nearest ``contracts/`` above this module."""

    model_config = SettingsConfigDict(env_prefix="BW_PUBLISH_")

    contracts_dir: Path | None = None


def python_regex(pattern: str) -> str:
    """Translate an ECMA-262 (``u`` flag) pattern from the contract files to Python ``re`` syntax.

    ``$`` becomes ``\\Z`` (Python's ``$`` also matches before a final newline), ``\\s``/``\\S`` and
    ``.`` get ECMA-262's exact character sets. ``\\b``, ``\\d``, ``\\w``, ``\\p`` and ``\\k`` (and
    their negations) mean different things in the two engines or do not exist in Python, so they
    raise ``ValueError``: contract patterns spell out their character classes instead.
    """
    out: list[str] = []
    in_class = False
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if char == "\\":
            if index + 1 >= len(pattern):
                raise ValueError(f"pattern ends with a lone backslash: {pattern!r}")
            escaped = pattern[index + 1]
            if escaped in _UNTRANSLATABLE_ESCAPES:
                raise ValueError(f"\\{escaped} is engine-dependent; spell it out: {pattern!r}")
            if escaped == "s":
                out.append(_ECMA_WHITESPACE if in_class else f"[{_ECMA_WHITESPACE}]")
            elif escaped == "S":
                if in_class:
                    raise ValueError(f"\\S inside a character class is not supported: {pattern!r}")
                out.append(f"[^{_ECMA_WHITESPACE}]")
            else:
                out.append(pattern[index : index + 2])
            index += 2
            continue
        if in_class:
            if char == "[":
                raise ValueError(f"'[' inside a character class must be escaped: {pattern!r}")
            if char == "]":
                in_class = False
            out.append(char)
        elif char == "[":
            if pattern.startswith(("[]", "[^]"), index):
                raise ValueError(f"empty character classes differ between engines: {pattern!r}")
            in_class = True
            out.append(char)
        elif char == "$":
            out.append(r"\Z")
        elif char == ".":
            out.append(_ECMA_DOT)
        else:
            out.append(char)
        index += 1
    if in_class:
        raise ValueError(f"unterminated character class: {pattern!r}")
    return "".join(out)


def _compile(pattern: object, where: str) -> re.Pattern[str]:
    if not isinstance(pattern, str):
        raise ContractUnavailableError(f"{where}: pattern must be a string")
    try:
        return re.compile(python_regex(pattern))
    except (ValueError, re.error) as error:
        raise ContractUnavailableError(f"{where}: unusable pattern: {error}") from error


def _quote(value: str, limit: int = 80) -> str:
    shown = value if len(value) <= limit else value[: limit - 3] + "..."
    return json.dumps(shown)


def _name(name: str) -> str:
    """A tag or attribute name taken from a document, shortened for messages."""
    return name if len(name) <= _NAME_LIMIT else name[: _NAME_LIMIT - 3] + "..."


# --- JSON helpers -------------------------------------------------------------------------------


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate key {key!r}")
        result[key] = value
    return result


def _read_json(path: Path) -> object:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise ContractUnavailableError(f"cannot read {path}: {error.strerror}") from error
    try:
        data: object = json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except ValueError as error:
        raise ContractUnavailableError(f"{path.name} is not valid JSON: {error}") from error
    return data


def _json_object(value: object, where: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ContractUnavailableError(f"{where}: expected a JSON object")
    return cast("dict[str, object]", value)


def _string_list(value: object, where: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ContractUnavailableError(f"{where}: expected a list of strings")
    items = cast("list[object]", value)
    strings = tuple(item for item in items if isinstance(item, str))
    if len(strings) != len(items):
        raise ContractUnavailableError(f"{where}: expected a list of strings")
    return strings


def _string_map(value: object, where: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, item in _json_object(value, where).items():
        if not isinstance(item, str):
            raise ContractUnavailableError(f"{where}.{key}: expected a string")
        result[key] = item
    return result


def _as_mapping(value: object) -> Mapping[str, object] | None:
    if isinstance(value, Mapping):
        return cast("Mapping[str, object]", value)
    return None


def _as_list(value: object) -> list[object] | None:
    if isinstance(value, list):
        return cast("list[object]", value)
    return None


def _as_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _pointer(*parts: str | int) -> str:
    return "".join("/" + str(part).replace("~", "~0").replace("/", "~1") for part in parts)


# --- Sanitizer profile --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SanitizerProfile:
    """``sanitizer-profile.json``, parsed and checked, with patterns compiled for Python."""

    version: int
    tags: frozenset[str]
    attributes: Mapping[str, frozenset[str]]
    class_patterns: Mapping[str, re.Pattern[str]]
    id_patterns: Mapping[str, re.Pattern[str]]
    url_patterns: Mapping[str, tuple[re.Pattern[str], ...]]
    value_patterns: Mapping[str, re.Pattern[str]]
    style_values: frozenset[str]
    forced_attributes: Mapping[str, Mapping[str, str]]
    link_rel: str
    drop_with_content: frozenset[str]
    clobber_prefix: str

    @classmethod
    def from_json(cls, data: object) -> SanitizerProfile:
        where = PROFILE_FILE
        raw = _json_object(data, where)
        keys = frozenset(raw)
        if keys != _PROFILE_KEYS:
            unknown, missing = sorted(keys - _PROFILE_KEYS), sorted(_PROFILE_KEYS - keys)
            raise ContractUnavailableError(f"{where}: unknown keys {unknown}, missing {missing}")
        version = _as_int(raw["version"])
        if version != PROFILE_VERSION:
            raise ContractUnavailableError(
                f"{where}: version {raw['version']!r}; this server implements version "
                f"{PROFILE_VERSION}"
            )
        url_patterns = {
            name: tuple(_compile(p, f"{where} urlPatterns.{name}") for p in _string_list(v, where))
            for name, v in _json_object(raw["urlPatterns"], f"{where} urlPatterns").items()
        }
        profile = cls(
            version=version,
            tags=frozenset(_string_list(raw["tags"], f"{where} tags")),
            attributes={
                tag: frozenset(_string_list(names, f"{where} attributes.{tag}"))
                for tag, names in _json_object(raw["attributes"], f"{where} attributes").items()
            },
            class_patterns={
                tag: _compile(pattern, f"{where} classPatterns.{tag}")
                for tag, pattern in _string_map(raw["classPatterns"], where).items()
            },
            id_patterns={
                tag: _compile(pattern, f"{where} idPatterns.{tag}")
                for tag, pattern in _string_map(raw["idPatterns"], where).items()
            },
            url_patterns=url_patterns,
            value_patterns={
                name: _compile(pattern, f"{where} valuePatterns.{name}")
                for name, pattern in _string_map(raw["valuePatterns"], where).items()
            },
            style_values=frozenset(_string_list(raw["styleValues"], f"{where} styleValues")),
            forced_attributes={
                tag: _string_map(values, f"{where} forcedAttributes.{tag}")
                for tag, values in _json_object(
                    raw["forcedAttributes"], f"{where} forcedAttributes"
                ).items()
            },
            link_rel=_string_map({"linkRel": raw["linkRel"]}, where)["linkRel"],
            drop_with_content=frozenset(
                _string_list(raw["dropWithContent"], f"{where} dropWithContent")
            ),
            clobber_prefix=_string_map({"clobberPrefix": raw["clobberPrefix"]}, where)[
                "clobberPrefix"
            ],
        )
        profile._check_rules()
        return profile

    def _check_rules(self) -> None:
        """Fail closed on a profile this code cannot apply (an attribute without a rule)."""
        where = PROFILE_FILE
        if not set(self.attributes) <= self.tags:
            raise ContractUnavailableError(f"{where}: attributes for tags that are not allowed")
        if self.drop_with_content & self.tags:
            raise ContractUnavailableError(f"{where}: a tag is both allowed and dropped")
        for tag, names in self.attributes.items():
            for name in names:
                if not self._has_rule(tag, name):
                    raise ContractUnavailableError(f"{where}: no value rule for {name} on <{tag}>")
        for tag, forced in self.forced_attributes.items():
            if not set(forced) <= self.attributes.get(tag, frozenset()):
                raise ContractUnavailableError(f"{where}: forced attributes on <{tag}> not allowed")

    def _has_rule(self, tag: str, name: str) -> bool:
        if name in _FREE_TEXT_ATTRIBUTES or name in _BOOLEAN_ATTRIBUTES:
            return True
        if name == "class":
            return tag in self.class_patterns
        if name == "id":
            return tag in self.id_patterns
        if name == "style":
            return bool(self.style_values)
        if name in self.url_patterns:
            return bool(self.url_patterns[name])
        return name in self.value_patterns

    def attribute_problem(self, tag: str, name: str, value: str | None) -> str | None:
        """Why ``name=value`` on an allowed ``<tag>`` breaks the profile; ``None`` if it's fine."""
        if tag == "a" and name == "rel":
            return None if value == self.link_rel else f'rel on <a> must be "{self.link_rel}"'
        if name not in self.attributes.get(tag, frozenset()):
            return f"attribute {_name(name)} on <{tag}> is not allowed"
        if name in _BOOLEAN_ATTRIBUTES:
            return None if value in (None, "", name) else f"{name} on <{tag}> must be boolean"
        if value is None:
            return f"attribute {name} on <{tag}> needs a value"
        if name in _FREE_TEXT_ATTRIBUTES:
            return None
        if name == "class":
            pattern = self.class_patterns[tag]
            tokens = [t for t in _ASCII_WHITESPACE_RE.split(value) if t]
            if not tokens:
                return f"empty class on <{tag}>"
            bad = [t for t in tokens if not pattern.search(t)]
            return f"class {_quote(' '.join(bad))} on <{tag}> is not allowed" if bad else None
        if name == "id":
            allowed = self.id_patterns[tag].search(value) is not None
        elif name == "style":
            allowed = value in self.style_values
        elif name in self.url_patterns:
            allowed = any(p.search(value) for p in self.url_patterns[name])
        else:
            allowed = self.value_patterns[name].search(value) is not None
        return None if allowed else f"{name}={_quote(value)} on <{tag}> is not allowed"


# --- HTML audit ---------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HtmlAudit:
    """What :meth:`PublishedContract.audit_html` found in an HTML fragment."""

    findings: tuple[str, ...]
    """Everything outside the sanitizer profile, and everything that keeps the fragment from being
    self-contained (unclosed elements, stray end tags, markup cut off at the end); empty when the
    fragment conforms."""
    headings: tuple[tuple[int, str], ...]
    """``(level, id)`` of every h2/h3 that carries an id, in document order."""
    ids: tuple[str, ...]
    """Every ``id`` attribute, in document order."""
    media: tuple[str, ...]
    """Every ``img src`` and ``a href`` that starts with ``/blog-media/``, in document order."""


class _HtmlAuditor(HTMLParser):
    """Checks markup against the profile with the standard library's parser (not ammonia).

    Sanitized HTML never contains a raw ``<`` in text, comments, declarations or processing
    instructions, so each of those is a finding too. Python's parser drops numeric references to
    control characters and noncharacters (``&#1;``) that browsers keep, so the values checked here
    could differ from what a browser sees: those references are findings as well.

    The portfolio inserts the fragment into its page, so it must be self-contained: every allowed
    element it opens is closed in order, it closes nothing it did not open, and it does not end
    inside a tag, comment or raw-text element. Otherwise it would reshape the page around it: an
    unterminated ``<img ... onerror=...`` swallows the page's next end tag and becomes a live
    element, a stray ``</div>`` closes the article container, and an unclosed ``<a>`` is
    re-opened around the rest of the page.
    """

    def __init__(self, profile: SanitizerProfile) -> None:
        super().__init__(convert_charrefs=False)
        self._profile = profile
        self.findings: list[str] = []
        self.headings: list[tuple[int, str]] = []
        self.ids: list[str] = []
        self.media: list[str] = []
        self._open: list[str] = []
        """Allowed non-void elements opened and not yet closed, outermost first."""
        self._open_count: Counter[str] = Counter()
        """How many of each tag ``_open`` holds (a list scan would be quadratic)."""
        self._refused: set[str] = set()
        """Disallowed tags already reported; their end tags add nothing."""
        self._end: tuple[int, int] | None = None
        """``getpos()`` at the end of the fragment, where the appended end tag must start."""
        self._ended = False

    def audit(self, html: str) -> None:
        """Parse ``html`` as the portfolio's page does: with more markup right after it.

        The fragment and the end tag that stands in for the page are parsed in one pass. If the
        fragment's last construct is unfinished, it absorbs that end tag, which then never starts
        at the fragment's end position.
        """
        self._end = (html.count("\n") + 1, len(html) - html.rfind("\n") - 1)
        self.feed(f"{html}</{_FRAGMENT_END}>")
        self.close()
        if not self._ended:
            self.findings.append(
                "the HTML ends inside a tag, comment or raw-text element, which would swallow "
                "the page markup after it"
            )
        self.findings.extend(f"<{tag}> is not closed" for tag in reversed(self._open))

    @override
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._element(tag, attrs)

    @override
    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # Browsers ignore the self-closing flag on HTML elements: <p/> opens a <p>.
        self._element(tag, attrs)

    @override
    def handle_endtag(self, tag: str) -> None:
        if tag == _FRAGMENT_END and self.getpos() == self._end:
            self._ended = True
            return
        if tag not in self._profile.tags or tag in _VOID_TAGS:
            if tag not in self._refused:
                self.findings.append(f"end tag </{_name(tag)}> is not allowed")
            return
        if not self._open_count[tag]:
            self.findings.append(f"end tag </{tag}> closes nothing")
            return
        while True:
            inner = self._open.pop()
            self._open_count[inner] -= 1
            if inner == tag:
                return
            self.findings.append(f"<{inner}> is not closed before </{tag}>")

    @override
    def handle_data(self, data: str) -> None:
        if "<" in data:
            self.findings.append("text contains an unescaped '<'")

    @override
    def handle_charref(self, name: str) -> None:
        self._check_reference(f"&#{name};", "text")

    @override
    def handle_comment(self, data: str) -> None:
        self.findings.append("comments are not allowed")

    @override
    def handle_decl(self, decl: str) -> None:
        self.findings.append("declarations (such as <!DOCTYPE>) are not allowed")

    @override
    def unknown_decl(self, data: str) -> None:
        self.findings.append("CDATA sections are not allowed")

    @override
    def handle_pi(self, data: str) -> None:
        self.findings.append("processing instructions are not allowed")

    def _check_reference(self, reference: str, where: str) -> None:
        if html_unescape(reference) == "":
            self.findings.append(
                f"character reference {reference} in {where} is dropped by Python but kept by "
                "browsers"
            )

    def _element(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        profile = self._profile
        if tag not in profile.tags:
            self._refused.add(tag)
            self.findings.append(f"tag <{_name(tag)}> is not allowed")
            return
        if tag not in _VOID_TAGS:
            self._open.append(tag)
            self._open_count[tag] += 1
        for match in _NUMERIC_CHARREF_RE.finditer(self.get_starttag_text() or ""):
            self._check_reference(match.group(), f"<{tag}>")
        values: dict[str, str | None] = {}
        for name, value in attrs:
            if name in values:
                self.findings.append(f"attribute {_name(name)} appears twice on <{tag}>")
                continue
            values[name] = value
            problem = profile.attribute_problem(tag, name, value)
            if problem is not None:
                self.findings.append(problem)
        for name, required in profile.forced_attributes.get(tag, {}).items():
            # A boolean attribute (required == name) may also be written bare: <input disabled>.
            present = name in values
            actual = values.get(name)
            if not present or not (
                actual == required or (required == name and actual in (None, ""))
            ):
                self.findings.append(f'<{tag}> must have {name}="{required}"')
        element_id = values.get("id")
        if element_id:
            self.ids.append(element_id)
            if tag in TOC_HEADING_TAGS:
                self.headings.append((int(tag[1]), element_id))
        reference = values.get(_MEDIA_ATTRIBUTES.get(tag, ""))
        if reference is not None and reference.startswith(MEDIA_PREFIX):
            self.media.append(reference)


# --- The contract -------------------------------------------------------------------------------


def find_contracts_dir() -> Path:
    """``BW_PUBLISH_CONTRACTS_DIR`` if set, else the nearest ``contracts/`` above this module."""
    configured = ContractSettings().contracts_dir
    if configured is not None:
        if (configured / SCHEMA_FILE).is_file():
            return configured
        raise ContractUnavailableError(
            f"BW_PUBLISH_CONTRACTS_DIR={configured} does not contain {SCHEMA_FILE}"
        )
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "contracts"
        if (candidate / SCHEMA_FILE).is_file():
            return candidate
    raise ContractUnavailableError(
        f"no contracts/{SCHEMA_FILE} above {here.parent}; set BW_PUBLISH_CONTRACTS_DIR"
    )


def _translate_schema(node: object) -> object:
    """A copy of the schema with every pattern translated by :func:`python_regex`."""
    if isinstance(node, dict):
        result: dict[str, object] = {}
        for key, value in cast("dict[str, object]", node).items():
            if key == "pattern" and isinstance(value, str):
                result[key] = python_regex(value)
            elif key == "patternProperties" and isinstance(value, dict):
                result[key] = {
                    python_regex(name): _translate_schema(subschema)
                    for name, subschema in cast("dict[str, object]", value).items()
                }
            elif key in _NOT_SCHEMA_KEYWORDS:
                result[key] = value
            else:
                result[key] = _translate_schema(value)
        return result
    if isinstance(node, list):
        return [_translate_schema(item) for item in cast("list[object]", node)]
    return node


def _def_pattern(definitions: Mapping[str, object], name: str) -> re.Pattern[str]:
    definition = _as_mapping(definitions.get(name))
    pattern = definition.get("pattern") if definition is not None else None
    return _compile(pattern, f"{SCHEMA_FILE} $defs.{name}")


@dataclass(frozen=True, slots=True)
class PublishedContract:
    """The loaded contract files. Use :func:`default_contract` unless you need another directory."""

    directory: Path
    schema: Mapping[str, object]
    profile: SanitizerProfile
    _validator: Validator = field(repr=False)
    _patterns: Mapping[str, re.Pattern[str]] = field(repr=False)

    @classmethod
    def load(cls, directory: Path | None = None) -> PublishedContract:
        """Read and check both contract files; raises :class:`ContractUnavailableError`."""
        root = find_contracts_dir() if directory is None else directory
        schema = _json_object(_read_json(root / SCHEMA_FILE), SCHEMA_FILE)
        if schema.get("$id") != SCHEMA_ID:
            raise ContractUnavailableError(f"{SCHEMA_FILE}: $id must be {SCHEMA_ID}")
        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError as error:
            raise ContractUnavailableError(f"{SCHEMA_FILE}: {error.message}") from error
        properties = _json_object(schema.get("properties"), f"{SCHEMA_FILE} properties")
        version = _as_mapping(properties.get("schemaVersion"))
        if version is None or version.get("const") != SCHEMA_VERSION:
            raise ContractUnavailableError(f"{SCHEMA_FILE}: schemaVersion must be {SCHEMA_VERSION}")
        definitions = _json_object(schema.get("$defs"), f"{SCHEMA_FILE} $defs")
        try:
            translated = cast("dict[str, object]", _translate_schema(schema))
        except ValueError as error:
            raise ContractUnavailableError(f"{SCHEMA_FILE}: {error}") from error
        return cls(
            directory=root,
            schema=schema,
            profile=SanitizerProfile.from_json(_read_json(root / PROFILE_FILE)),
            _validator=Draft202012Validator(
                translated, format_checker=Draft202012Validator.FORMAT_CHECKER
            ),
            _patterns={name: _def_pattern(definitions, name) for name in _INVARIANT_DEFS},
        )

    def audit_html(self, html: str) -> HtmlAudit:
        """Check an HTML fragment against the sanitizer profile and collect ids and media."""
        auditor = _HtmlAuditor(self.profile)
        try:
            auditor.audit(html)
        except Exception as error:  # any parser failure means "reject"
            auditor.findings.append(f"the HTML could not be parsed ({type(error).__name__})")
        return HtmlAudit(
            findings=tuple(auditor.findings),
            headings=tuple(auditor.headings),
            ids=tuple(auditor.ids),
            media=tuple(auditor.media),
        )

    def validate(
        self, doc: Mapping[str, object], *, now: datetime | None = None
    ) -> list[ContractError]:
        """Every way ``doc`` breaks the contract; an empty list means it may be written.

        ``now`` (timezone-aware, default: the current time) is the reference for "not in the
        future" checks.
        """
        moment = datetime.now(UTC) if now is None else now
        if moment.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        errors = self._schema_errors(doc)
        errors.extend(_Invariants(doc, moment, self.audit_html, self._patterns).check())
        return errors

    def _schema_errors(self, doc: Mapping[str, object]) -> list[ContractError]:
        try:
            found = list(self._validator.iter_errors(cast("Mapping[str, Any]", doc)))
        except RecursionError:
            return [ContractError("schema-depth", "", "the document nests too deeply to validate")]
        found.sort(key=_error_sort_key)
        return [_schema_error(error, doc) for error in found]


def _error_sort_key(error: ValidationError) -> tuple[tuple[tuple[int, int, str], ...], str]:
    path = tuple((0, p, "") if isinstance(p, int) else (1, 0, p) for p in error.absolute_path)
    return path, error.message


def _schema_error(error: ValidationError, doc: Mapping[str, object]) -> ContractError:
    parts: list[str | int] = list(error.absolute_path)
    keyword = error.validator if isinstance(error.validator, str) else "false"
    code = "schema-" + _KEBAB_RE.sub("-", keyword).lower()
    schema: Mapping[str, object] = error.schema if isinstance(error.schema, Mapping) else {}
    description = schema.get("description")
    expected: object = error.validator_value
    if "propertyNames" in error.relative_schema_path and isinstance(error.instance, str):
        status = doc.get("status")
        shown = json.dumps(status) if isinstance(status, str) else "this status"
        return ContractError(
            "schema-status-fields",
            _pointer(*parts, error.instance),
            f"is not allowed when status is {shown}",
        )
    match keyword:
        case "required" | "additionalProperties":
            message = _shorten(error.message)  # quotes the document's unexpected keys
        case "not" | "false":
            message = description if isinstance(description, str) else "is not allowed here"
        case "type":
            message = f"must be of type {json.dumps(expected)}"
        case "const":
            message = f"must be {json.dumps(expected)}"
        case "enum":
            options = cast("list[object]", expected) if isinstance(expected, list) else []
            message = "must be one of " + ", ".join(json.dumps(v) for v in options)
        case "pattern":
            message = (
                f"must be {description}"
                if isinstance(description, str)
                else f"must match {json.dumps(expected)}"
            )
        case "format":
            message = f"is not a valid {expected}"
        case "minLength":
            message = "must not be empty" if expected == 1 else f"needs {expected}+ characters"
        case "maxLength":
            message = f"must be at most {expected} characters long"
        case "minimum":
            message = f"must be at least {expected}"
        case "maximum":
            message = f"must be at most {expected}"
        case "maxItems":
            message = f"must have at most {expected} items"
        case "uniqueItems":
            message = "must not contain duplicates"
        case "minProperties":
            message = "must not be empty"
        case _:
            message = _shorten(error.message)
    return ContractError(code, _pointer(*parts), message)


def _shorten(message: str, limit: int = 200) -> str:
    return message if len(message) <= limit else message[: limit - 3] + "..."


class _Invariants:
    """The rules JSON Schema cannot express. Each check skips values the schema already rejects."""

    def __init__(
        self,
        doc: Mapping[str, object],
        now: datetime,
        audit: Callable[[str], HtmlAudit],
        patterns: Mapping[str, re.Pattern[str]],
    ) -> None:
        self._audit = audit
        self._patterns = patterns
        self._doc = doc
        self._now = now
        self._errors: list[ContractError] = []
        doc_id = doc.get("id")
        self._id = doc_id if isinstance(doc_id, str) else None
        # The media folder is /blog-media/<id>/: undefined while id is invalid or differs from slug.
        valid_id = self._matches("slug", doc_id) and doc.get("slug") == doc_id
        self._folder = self._id if valid_id else None
        self._published: datetime | None = None
        self._updated: datetime | None = None
        self._asset_paths: set[str] = set()

    def check(self) -> list[ContractError]:
        self._identity()
        self._instants()
        self._date_field()
        self._series()
        self._unique("toc", "id", "duplicate-toc-id")
        self._unique("citations", "id", "duplicate-citation-id")
        self._citation_dates()
        self._corrections()
        self._assets()
        self._images()
        html = self._doc.get("htmlContent")
        if isinstance(html, str):
            self._html(html)
        self._sizes()
        return self._errors

    def _add(self, code: str, path: str, message: str) -> None:
        self._errors.append(ContractError(code, path, message))

    def _matches(self, definition: str, value: object) -> bool:
        return isinstance(value, str) and self._patterns[definition].search(value) is not None

    def _items(self, key: str) -> Iterator[tuple[int, Mapping[str, object]]]:
        for index, item in enumerate(_as_list(self._doc.get(key)) or ()):
            mapping = _as_mapping(item)
            if mapping is not None:
                yield index, mapping

    def _identity(self) -> None:
        slug = self._doc.get("slug")
        if self._id is not None and isinstance(slug, str) and self._id != slug:
            self._add(
                "id-slug-mismatch",
                "/id",
                f"must equal slug ({_quote(slug)}): the portfolio point-reads item(slug, slug)",
            )
        target = self._doc.get("redirectTo")
        if self._doc.get("status") == "redirect" and target is not None and target == self._id:
            self._add("redirect-to-self", "/redirectTo", "must name another document, not this one")

    def _instants(self) -> None:
        for key in ("publishedAt", "updatedAt"):
            value = self._doc.get(key)
            if not isinstance(value, str) or not self._matches("utcTimestamp", value):
                continue
            try:
                instant = datetime(
                    int(value[0:4]),
                    int(value[5:7]),
                    int(value[8:10]),
                    int(value[11:13]),
                    int(value[14:16]),
                    int(value[17:19]),
                    tzinfo=UTC,
                )
            except ValueError:
                self._add("invalid-timestamp", f"/{key}", "is not a real calendar date and time")
                continue
            if key == "publishedAt":
                self._published = instant
            else:
                self._updated = instant
        horizon = self._now + FUTURE_TOLERANCE
        published, updated = self._published, self._updated
        if self._doc.get("status") == "published" and published is not None and published > horizon:
            self._add(
                "published-in-future",
                "/publishedAt",
                "is in the future: publish at the publication instant (readers never render a "
                "document before its publishedAt)",
            )
        if updated is not None and updated > horizon:
            self._add("updated-in-future", "/updatedAt", "is in the future")
        if published is not None and updated is not None and updated < published:
            self._add("updated-before-published", "/updatedAt", "is earlier than publishedAt")

    def _full_date(self, value: object) -> date | None:
        if not isinstance(value, str) or not self._matches("fullDate", value):
            return None
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None  # the schema's date format check reports it

    def _date_field(self) -> None:
        value = self._full_date(self._doc.get("date"))
        if value is not None and self._published is not None:
            expected = self._published.date()
            if value != expected:
                self._add(
                    "date-mismatch", "/date", f"must be the UTC date of publishedAt ({expected})"
                )

    def _series(self) -> None:
        series = _as_mapping(self._doc.get("series"))
        if series is None:
            return
        part, total = _as_int(series.get("part")), _as_int(series.get("total"))
        if part is not None and total is not None and total < part:
            self._add("series-total-before-part", "/series/total", "must be at least series.part")

    def _unique(self, key: str, name: str, code: str) -> None:
        seen: set[str] = set()
        for index, item in self._items(key):
            value = item.get(name)
            if isinstance(value, str):
                if value in seen:
                    self._add(code, _pointer(key, index, name), f"repeats an earlier {name}")
                seen.add(value)

    def _citation_dates(self) -> None:
        for index, item in self._items("citations"):
            value = item.get("date")
            if (
                isinstance(value, str)
                and len(value) == len("YYYY-MM-DD")
                and self._matches("partialDate", value)
            ):
                try:
                    date.fromisoformat(value)
                except ValueError:
                    self._add(
                        "invalid-date",
                        _pointer("citations", index, "date"),
                        "is not a real calendar date",
                    )

    def _corrections(self) -> None:
        previous: date | None = None
        for index, item in self._items("corrections"):
            value = self._full_date(item.get("date"))
            if value is None:
                continue
            path = _pointer("corrections", index, "date")
            if self._published is not None and value < self._published.date():
                self._add("correction-date-out-of-range", path, "is before publishedAt")
            elif self._updated is not None and value > self._updated.date():
                self._add("correction-date-out-of-range", path, "is after updatedAt")
            if previous is not None and value < previous:
                self._add(
                    "corrections-out-of-order", path, "is earlier than the correction before it"
                )
            previous = value

    def _assets(self) -> None:
        for index, item in self._items("assets"):
            path, blob = item.get("path"), item.get("blob")
            if not isinstance(path, str):
                continue
            if path in self._asset_paths:
                self._add(
                    "duplicate-asset-path", _pointer("assets", index, "path"), "repeats a path"
                )
            self._asset_paths.add(path)
            if (
                self._folder is None
                or not self._matches("assetPath", path)
                or not self._matches("blobName", blob)
                or not isinstance(blob, str)
            ):
                continue
            path_ok = path.startswith(f"{MEDIA_PREFIX}{self._folder}/")
            blob_ok = blob.startswith(f"{self._folder}/")
            if not path_ok:
                self._add(
                    "asset-location",
                    _pointer("assets", index, "path"),
                    f"must be under {MEDIA_PREFIX}{self._folder}/",
                )
            if not blob_ok:
                self._add(
                    "asset-location",
                    _pointer("assets", index, "blob"),
                    f"must be under {self._folder}/",
                )
            if path_ok and blob_ok and path != MEDIA_PREFIX + blob:
                self._add(
                    "asset-location",
                    _pointer("assets", index, "blob"),
                    f"must name the same file as path ({MEDIA_PREFIX}<blob> == path)",
                )

    def _images(self) -> None:
        for key in ("heroImage", "ogImage"):
            image = _as_mapping(self._doc.get(key))
            url = image.get("url") if image is not None else None
            if isinstance(url, str) and url.startswith("/") and url not in self._asset_paths:
                self._add(
                    "image-not-in-assets",
                    _pointer(key, "url"),
                    "is site-relative but not one of assets[].path (the portfolio only serves "
                    "files listed in assets)",
                )

    def _html(self, html: str) -> None:
        audit = self._audit(html)
        for finding in audit.findings[:MAX_HTML_FINDINGS]:
            self._add("html-outside-profile", "/htmlContent", finding)
        if len(audit.findings) > MAX_HTML_FINDINGS:
            hidden = len(audit.findings) - MAX_HTML_FINDINGS
            self._add("html-outside-profile", "/htmlContent", f"... and {hidden} more findings")
        for element_id, count in Counter(audit.ids).items():
            if count > 1:
                self._add(
                    "duplicate-html-id", "/htmlContent", f"id {_quote(element_id)} is used {count}x"
                )
        for reference in dict.fromkeys(audit.media):
            if reference not in self._asset_paths:
                self._add(
                    "media-not-in-assets",
                    "/htmlContent",
                    f"references {_quote(reference)}, which is not one of assets[].path",
                )
        self._toc(audit.headings)

    def _toc(self, headings: Sequence[tuple[int, str]]) -> None:
        entries = _as_list(self._doc.get("toc"))
        if entries is None:
            return
        toc: list[tuple[int, str]] = []
        for entry in entries:
            item = _as_mapping(entry)
            level = _as_int(item.get("level")) if item is not None else None
            heading_id = item.get("id") if item is not None else None
            if level not in TOC_LEVELS or not isinstance(heading_id, str):
                return  # malformed entries are the schema's to report
            if not self._matches("slug", heading_id):
                return
            toc.append((level, heading_id))
        if toc == list(headings):
            return
        index = next(
            i
            for i in range(max(len(toc), len(headings)))
            if i >= len(toc) or i >= len(headings) or toc[i] != headings[i]
        )
        ours = _heading(toc[index] if index < len(toc) else None)
        theirs = _heading(headings[index] if index < len(headings) else None)
        self._add(
            "toc-mismatch",
            _pointer("toc", index) if index < len(toc) else "/toc",
            f"must list the h2/h3 headings of htmlContent in order: entry {index} is {ours}, "
            f"htmlContent has {theirs}",
        )

    def _sizes(self) -> None:
        markdown = self._doc.get("mdxContent")
        if isinstance(markdown, str):
            size = len(markdown.encode("utf-8", "surrogatepass"))
            if size > MAX_MARKDOWN_BYTES:
                self._add(
                    "markdown-too-large",
                    "/mdxContent",
                    f"is {size} bytes of UTF-8; the limit is {MAX_MARKDOWN_BYTES}",
                )
        try:
            text = json.dumps(self._doc, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        except RecursionError:
            self._add("not-json", "", "the document nests too deeply to serialize")
            return
        except (TypeError, ValueError):
            self._add(
                "not-json",
                "",
                "the document must hold only JSON values (objects, arrays, strings, finite "
                "numbers, booleans and null)",
            )
            return
        try:
            size = len(text.encode("utf-8"))
        except UnicodeEncodeError:
            self._add("invalid-unicode", "", "contains unpaired surrogates, which UTF-8 can't hold")
            size = len(text.encode("utf-8", "surrogatepass"))
        if size > MAX_DOCUMENT_BYTES:
            self._add(
                "document-too-large",
                "",
                f"is {size} bytes of UTF-8 JSON; Cosmos DB items must stay under "
                f"{MAX_DOCUMENT_BYTES}",
            )


def _heading(entry: tuple[int, str] | None) -> str:
    return "nothing" if entry is None else f"h{entry[0]}#{entry[1]}"


@cache
def default_contract() -> PublishedContract:
    """The contract from :func:`find_contracts_dir`, loaded once per process."""
    return PublishedContract.load()


def validate_published_doc(
    doc: Mapping[str, object], *, now: datetime | None = None
) -> list[ContractError]:
    """Validate a published-blogs document against the v2 contract.

    Returns every problem found (an empty list means the document may be written). Raises
    :class:`ContractUnavailableError` when the contract files are missing or malformed: the
    caller must not publish then. CPU-bound: use ``asyncio.to_thread`` from async code.
    """
    return default_contract().validate(doc, now=now)


def audit_html(html: str) -> HtmlAudit:
    """Check an HTML fragment against ``contracts/sanitizer-profile.json``."""
    return default_contract().audit_html(html)
