"""The frozen contract files: schema shape, cross-file consistency, fixtures, loading."""

from __future__ import annotations

import json
import re
import shutil
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, Final, cast

import pytest
from jsonschema import Draft202012Validator

from blogwriter.publish.contract import (
    PROFILE_FILE,
    SCHEMA_FILE,
    SCHEMA_ID,
    TOC_HEADING_TAGS,
    TOC_LEVELS,
    ContractUnavailableError,
    PublishedContract,
    find_contracts_dir,
    python_regex,
)

Json = dict[str, Any]

V1_FIELDS: Final = (
    "id",
    "slug",
    "title",
    "excerpt",
    "sourceUrl",
    "publishedAt",
    "updatedAt",
    "htmlContent",
    "mdxContent",
    "tags",
    "date",
    "sourceType",
)
V2_FIELDS: Final = (
    "schemaVersion",
    "status",
    "redirectTo",
    "pillar",
    "format",
    "series",
    "heroImage",
    "ogImage",
    "assets",
    "readingTimeMinutes",
    "wordCount",
    "toc",
    "citations",
    "corrections",
    "canonicalUrl",
    "seo",
)


def _load(path: Path) -> Json:
    return cast("Json", json.loads(path.read_text(encoding="utf-8")))


@pytest.fixture
def schema(contracts_dir: Path) -> Json:
    return _load(contracts_dir / SCHEMA_FILE)


@pytest.fixture
def profile(contracts_dir: Path) -> Json:
    return _load(contracts_dir / PROFILE_FILE)


def _walk(node: object) -> Iterator[tuple[str, object]]:
    """Every (key, value) pair in a JSON document, depth first."""
    if isinstance(node, dict):
        for key, value in cast("dict[str, object]", node).items():
            yield key, value
            yield from _walk(value)
    elif isinstance(node, list):
        for item in cast("list[object]", node):
            yield from _walk(item)


# --- Schema -----------------------------------------------------------------------------------


def test_schema_is_valid_draft_2020_12(schema: Json) -> None:
    Draft202012Validator.check_schema(schema)
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["$id"] == SCHEMA_ID


def test_schema_keeps_every_v1_field_and_adds_the_v2_fields(schema: Json) -> None:
    properties = set(schema["properties"])
    assert set(V1_FIELDS) <= properties
    assert set(V2_FIELDS) <= properties
    assert properties == set(V1_FIELDS) | set(V2_FIELDS)
    assert schema["additionalProperties"] is False


def test_schema_version_and_statuses(schema: Json) -> None:
    assert schema["properties"]["schemaVersion"] == {
        "const": 2,
        "description": schema["properties"]["schemaVersion"]["description"],
    }
    assert schema["properties"]["status"]["enum"] == ["published", "redirect", "unpublished"]
    assert schema["required"] == ["id", "slug", "schemaVersion", "status", "updatedAt"]


def test_published_documents_require_every_rendered_field(schema: Json) -> None:
    published = next(
        branch["then"]
        for branch in schema["allOf"]
        if branch["if"]["properties"]["status"]["const"] == "published"
    )
    assert set(published["required"]) == {
        "title",
        "excerpt",
        "htmlContent",
        "publishedAt",
        "tags",
        "pillar",
        "format",
        "heroImage",
        "ogImage",
        "assets",
        "readingTimeMinutes",
        "wordCount",
        "toc",
        "citations",
        "corrections",
    }


def test_stubs_carry_no_content(schema: Json) -> None:
    allowed = {
        branch["if"]["properties"]["status"]["const"]: set(branch["then"]["propertyNames"]["enum"])
        for branch in schema["allOf"]
        if "propertyNames" in branch["then"]
    }
    system = {"_rid", "_self", "_etag", "_attachments", "_ts", "_lsn"}
    assert allowed == {
        "redirect": {"id", "slug", "schemaVersion", "status", "redirectTo", "updatedAt"} | system,
        "unpublished": {"id", "slug", "schemaVersion", "status", "updatedAt"} | system,
    }


def test_only_formats_both_engines_enforce_alike(schema: Json) -> None:
    """Python's jsonschema lacks date-time and uri checkers; patterns carry those rules."""
    formats = {value for key, value in _walk(schema) if key == "format" and isinstance(value, str)}
    assert formats == {"date-time", "date"}


def test_every_schema_pattern_translates_for_python(schema: Json) -> None:
    patterns = [value for key, value in _walk(schema) if key == "pattern"]
    assert len(patterns) >= 10
    for pattern in patterns:
        assert isinstance(pattern, str)
        re.compile(python_regex(pattern))


def test_url_rule_is_the_sanitizer_src_rule(schema: Json, profile: Json) -> None:
    src: list[str] = profile["urlPatterns"]["src"]
    assert all(p.startswith("^") and p.endswith("$") for p in src)
    inner = "|".join(p[1:-1] for p in src)
    assert schema["$defs"]["url"]["pattern"] == f"^(?:{inner})$"
    assert schema["$defs"]["httpsUrl"]["pattern"] == src[0]


def test_heading_ids_match_the_sanitizer_id_rule(schema: Json, profile: Json) -> None:
    id_tags = {tag for tag in profile["idPatterns"] if re.fullmatch(r"h[1-6]", tag)}
    assert tuple(sorted(id_tags)) == TOC_HEADING_TAGS
    for tag in id_tags:
        assert profile["idPatterns"][tag] == schema["$defs"]["slug"]["pattern"]
    toc_levels = schema["properties"]["toc"]["items"]["properties"]["level"]["enum"]
    assert tuple(toc_levels) == TOC_LEVELS == tuple(int(tag[1]) for tag in TOC_HEADING_TAGS)


def test_dialect_limits(schema: Json) -> None:
    """Slugs, tags and plain-text lengths match docs/v2/article-dialect.md frontmatter rules."""
    definitions = schema["$defs"]
    assert definitions["slug"]["maxLength"] == 80
    assert definitions["tag"]["maxLength"] == 40
    assert definitions["sourceId"]["maxLength"] == 64
    assert schema["properties"]["title"]["maxLength"] == 150
    assert schema["properties"]["excerpt"]["maxLength"] == 400
    assert schema["properties"]["tags"]["maxItems"] == 10


# --- Sanitizer profile ------------------------------------------------------------------------


def test_profile_is_the_spike_profile_plus_the_freeze_changes(profile: Json) -> None:
    assert profile["version"] == 1
    assert profile["clobberPrefix"] == ""
    assert profile["linkRel"] == "noopener noreferrer"
    assert profile["urlPatterns"]["href"] == [
        r"^https://[^\s\\\x00-\x1f\x7f]+$",
        r"^mailto:[^\s\\\x00-\x1f\x7f]+$",
        r"^#[A-Za-z0-9._:-]+$",
        r"^/(?![/\\])[^\s\\\x00-\x1f\x7f]*$",
    ]
    assert profile["urlPatterns"]["src"] == [
        r"^https://[^\s\\\x00-\x1f\x7f]+$",
        r"^/(?![/\\])[^\s\\\x00-\x1f\x7f]*$",
    ]


@pytest.mark.parametrize(
    ("value", "allowed"),
    [
        ("https://example.com/a?b=c", True),
        ("/blog-media/post/a.png", True),
        ("https://x/\x01y", False),  # C0 control (the reviewer's freeze-time addition)
        ("https://x/\x1fy", False),
        ("https://x/\x7fy", False),  # DEL
        ("/a\x00b", False),
        ("https://x/\ufeffy", False),  # ECMA-262 \s includes U+FEFF
        ("//evil.example", False),
        ("/\\evil.example", False),
        ("http://example.com", False),
    ],
)
def test_url_rules_reject_controls(profile: Json, value: str, allowed: bool) -> None:
    rules = [re.compile(python_regex(p)) for p in profile["urlPatterns"]["src"]]
    assert any(rule.search(value) for rule in rules) is allowed


def test_profile_patterns_translate_for_python(profile: Json) -> None:
    groups = ("classPatterns", "idPatterns", "valuePatterns")
    patterns = [p for group in groups for p in profile[group].values()]
    patterns += [p for rules in profile["urlPatterns"].values() for p in rules]
    for pattern in patterns:
        re.compile(python_regex(pattern))


# --- Fixtures ---------------------------------------------------------------------------------


def test_corpus_matches_its_manifest(contracts_dir: Path) -> None:
    corpus = contracts_dir / "fixtures" / "corpus"
    manifest = _load(corpus / "manifest.json")
    sources = {p.name for p in corpus.glob("*.md")}
    assert len(sources) == 22
    assert set(manifest["files"]) == sources
    assert {p.name for p in (corpus / "normalized").glob("*.md")} == sources
    for path in corpus.rglob("*.md"):
        path.read_bytes().decode("utf-8")  # strict UTF-8


def test_xss_corpus_is_complete(contracts_dir: Path) -> None:
    xss = contracts_dir / "fixtures" / "xss"
    assert len(list((xss / "markdown").glob("*.md"))) == 24
    assert len(list((xss / "html").glob("*.html"))) == 20


def test_fixture_bytes_are_protected_from_eol_conversion(contracts_dir: Path) -> None:
    attributes = (contracts_dir / "fixtures" / ".gitattributes").read_text(encoding="utf-8")
    assert "* -text" in attributes.splitlines()


# --- Loading fails closed ---------------------------------------------------------------------


@pytest.fixture
def copy_dir(tmp_path: Path, contracts_dir: Path) -> Path:
    target = tmp_path / "contracts"
    target.mkdir()
    for name in (SCHEMA_FILE, PROFILE_FILE):
        shutil.copy(contracts_dir / name, target / name)
    return target


def _edit(path: Path, change: Callable[[Json], None]) -> None:
    data = _load(path)
    change(data)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_a_copy_loads(copy_dir: Path) -> None:
    assert PublishedContract.load(copy_dir).directory == copy_dir


def _set_version(data: Json) -> None:
    data["version"] = 2


def _add_unknown_key(data: Json) -> None:
    data["maxDepth"] = 20


def _attribute_without_rule(data: Json) -> None:
    data["attributes"]["figure"].append("data-x")


def _bad_regex(data: Json) -> None:
    data["classPatterns"]["span"] = "^(unclosed$"


def _engine_dependent_regex(data: Json) -> None:
    data["valuePatterns"]["start"] = r"^\d+$"


def _forced_but_not_allowed(data: Json) -> None:
    data["forcedAttributes"]["input"]["name"] = "x"


def _allowed_and_dropped(data: Json) -> None:
    data["dropWithContent"].append("p")


def _attributes_for_unknown_tag(data: Json) -> None:
    data["attributes"]["iframe"] = ["src"]


def _tags_not_strings(data: Json) -> None:
    data["tags"] = ["a", 1]


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (_set_version, "version"),
        (_add_unknown_key, "unknown keys"),
        (_attribute_without_rule, "no value rule"),
        (_bad_regex, "unusable pattern"),
        (_engine_dependent_regex, "engine-dependent"),
        (_forced_but_not_allowed, "forced attributes"),
        (_allowed_and_dropped, "both allowed and dropped"),
        (_attributes_for_unknown_tag, "attributes for tags"),
        (_tags_not_strings, "list of strings"),
    ],
)
def test_a_profile_this_code_cannot_apply_is_refused(
    copy_dir: Path, change: Callable[[Json], None], message: str
) -> None:
    _edit(copy_dir / PROFILE_FILE, change)
    with pytest.raises(ContractUnavailableError, match=message):
        PublishedContract.load(copy_dir)


def _wrong_id(data: Json) -> None:
    data["$id"] = "urn:other"


def _wrong_version(data: Json) -> None:
    data["properties"]["schemaVersion"]["const"] = 3


def _invalid_schema(data: Json) -> None:
    data["type"] = "objekt"


def _python_only_regex(data: Json) -> None:
    data["$defs"]["slug"]["pattern"] = r"^\w+$"


def _missing_definition(data: Json) -> None:
    del data["$defs"]["assetPath"]["pattern"]


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (_wrong_id, r"\$id"),
        (_wrong_version, "schemaVersion"),
        (_invalid_schema, "is not valid"),
        (_python_only_regex, "engine-dependent"),
        (_missing_definition, "assetPath"),
    ],
)
def test_a_schema_this_code_cannot_apply_is_refused(
    copy_dir: Path, change: Callable[[Json], None], message: str
) -> None:
    _edit(copy_dir / SCHEMA_FILE, change)
    with pytest.raises(ContractUnavailableError, match=message):
        PublishedContract.load(copy_dir)


def test_missing_and_malformed_files_are_refused(copy_dir: Path) -> None:
    (copy_dir / PROFILE_FILE).unlink()
    with pytest.raises(ContractUnavailableError, match="cannot read"):
        PublishedContract.load(copy_dir)
    (copy_dir / PROFILE_FILE).write_text("{not json", encoding="utf-8")
    with pytest.raises(ContractUnavailableError, match="not valid JSON"):
        PublishedContract.load(copy_dir)
    (copy_dir / PROFILE_FILE).write_text('{"version": 1, "version": 1}', encoding="utf-8")
    with pytest.raises(ContractUnavailableError, match="duplicate key"):
        PublishedContract.load(copy_dir)
    (copy_dir / SCHEMA_FILE).write_text("[]", encoding="utf-8")
    with pytest.raises(ContractUnavailableError, match="JSON object"):
        PublishedContract.load(copy_dir)


# --- Locating the contract --------------------------------------------------------------------


def test_default_location_is_the_repository_contracts_dir(
    contracts_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("BW_PUBLISH_CONTRACTS_DIR", raising=False)
    assert find_contracts_dir() == contracts_dir


def test_environment_override(copy_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BW_PUBLISH_CONTRACTS_DIR", str(copy_dir))
    assert find_contracts_dir() == copy_dir
    assert PublishedContract.load().directory == copy_dir


def test_environment_override_without_a_schema_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BW_PUBLISH_CONTRACTS_DIR", str(tmp_path))
    with pytest.raises(ContractUnavailableError, match="BW_PUBLISH_CONTRACTS_DIR"):
        find_contracts_dir()
