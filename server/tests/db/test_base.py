"""Registry, document rules and errors in blogwriter.db.base (plan: "Data")."""

from __future__ import annotations

import json
import re
from collections import OrderedDict
from enum import IntEnum, StrEnum
from pathlib import Path
from typing import Any

import pytest

from blogwriter.db import (
    CONTAINERS,
    Conflict,
    ContainerCheckFailed,
    InvalidRequest,
    NotFound,
    PreconditionFailed,
    StoreError,
    StoreUnavailable,
)
from blogwriter.db.base import (
    MAX_DOCUMENT_BYTES,
    MAX_ID_LENGTH,
    MAX_PARTITION_KEY_LENGTH,
    UNINDEXED_FIELDS,
    doc_from_stored,
    partition_key_path,
    prepare_write,
    resolve_partition_key,
    validate_etag,
    validate_id,
)

TERRAFORM = Path(__file__).resolve().parents[3] / "infra" / "v2" / "data" / "main.tf"


class Color(StrEnum):
    RED = "red"


class Level(IntEnum):
    HIGH = 3


def terraform_containers() -> dict[str, str]:
    text = TERRAFORM.read_text(encoding="utf-8")
    entries = re.findall(r'^\s*"([a-z0-9-]+)"\s*=\s*\{\s*partition_key\s*=\s*"([^"]+)"', text, re.M)
    return dict(entries)


def _top_level_fields(paths: str) -> set[str]:
    fields: set[str] = set()
    for path in re.findall(r'"([^"]+)"', paths):
        match = re.fullmatch(r"/([A-Za-z0-9_]+)/\*", path)
        assert match is not None, f"not a top-level exclusion: {path}"
        fields.add(match.group(1))
    return fields


def terraform_unindexed() -> dict[str, set[str]]:
    text = TERRAFORM.read_text(encoding="utf-8")
    shared = re.search(r"^\s*excluded_paths\s*=\s*\[([^\]]*)\]", text, re.M)
    assert shared is not None
    extras = re.findall(
        r'^\s*"([a-z0-9-]+)"\s*=\s*\{[^}\n]*extra_excluded_paths\s*=\s*\[([^\]]*)\]', text, re.M
    )
    base = _top_level_fields(shared.group(1))
    return {name: base | _top_level_fields(paths) for name, paths in extras}


# --------------------------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------------------------


def test_registry_mirrors_terraform() -> None:
    declared = terraform_containers()
    assert len(declared) == 15
    assert dict(CONTAINERS) == declared


def test_unindexed_fields_mirror_terraform() -> None:
    declared = terraform_unindexed()
    assert set(declared) == set(CONTAINERS)
    assert {name: set(fields) for name, fields in UNINDEXED_FIELDS.items()} == declared
    assert "events" in UNINDEXED_FIELDS["jobs"]
    assert "payload" in UNINDEXED_FIELDS["posts"]


def test_large_text_fields_are_not_indexed() -> None:
    """Contract I-8: the large text fields and reports nobody filters or sorts on."""
    assert {"body", "text"} <= UNINDEXED_FIELDS["stories"]  # style guides, voice samples
    assert {"payload", "displayText", "finalText", "aiDraftText"} <= UNINDEXED_FIELDS["posts"]
    assert "pastedText" in UNINDEXED_FIELDS["engagement"]
    assert {"gates", "servedBy"} <= UNINDEXED_FIELDS["articles"]
    for container in CONTAINERS:
        assert {"embedding", "markdown"} <= UNINDEXED_FIELDS[container]
    assert "text" not in UNINDEXED_FIELDS["ideas"]  # only the containers that hold them


def test_published_blogs_contract_partition_keys() -> None:
    assert CONTAINERS["published-blogs"] == "/id"
    assert CONTAINERS["published-blogs-staging"] == "/id"


def test_registry_is_read_only() -> None:
    with pytest.raises(TypeError):
        CONTAINERS["drafts"] = "/id"  # pyright: ignore[reportIndexIssue]


@pytest.mark.parametrize("name", ["drafts", "", "JOBS", None, 3])
def test_unknown_containers(name: object) -> None:
    with pytest.raises(InvalidRequest):
        partition_key_path(name)


# --------------------------------------------------------------------------------------------
# Ids, etags and partition keys
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "item_id",
    [
        "a",
        "3f2c8a9e-7d1b-4c5e-9f0a-1b2c3d4e5f60",
        "my-first-post",
        "Caf\N{LATIN SMALL LETTER E WITH ACUTE}.v2",
        "with space inside",
        "x" * MAX_ID_LENGTH,
    ],
)
def test_valid_ids(item_id: str) -> None:
    assert validate_id(item_id) == item_id


def test_validate_id_returns_a_plain_string() -> None:
    value = validate_id(Color.RED)
    assert value == "red"
    assert type(value) is str


@pytest.mark.parametrize(
    "item_id",
    ["", "a/b", "a\\b", "a?b", "a#b", "a\x7fb", "a\rb", " a", "a ", "x" * (MAX_ID_LENGTH + 1), 5],
)
def test_invalid_ids(item_id: object) -> None:
    with pytest.raises(InvalidRequest):
        validate_id(item_id)


def test_validate_etag() -> None:
    assert validate_etag('"abc"') == '"abc"'
    for bad in ("", None, 3):
        with pytest.raises(InvalidRequest):
            validate_etag(bad)


def test_resolve_partition_key() -> None:
    assert resolve_partition_key("jobs", "j1", None) == "j1"
    assert resolve_partition_key("jobs", "j1", "other") == "other"
    assert resolve_partition_key("article-versions", "v1", "a1") == "a1"
    with pytest.raises(InvalidRequest):
        resolve_partition_key("article-versions", "v1", None)
    with pytest.raises(InvalidRequest):
        resolve_partition_key("jobs", "j1", "")
    with pytest.raises(InvalidRequest):
        resolve_partition_key("jobs", "j1", "x" * (MAX_PARTITION_KEY_LENGTH + 1))


@pytest.mark.parametrize(
    ("container", "body", "expected"),
    [
        ("jobs", {"id": "j1"}, "j1"),
        ("article-versions", {"id": "v1", "articleId": "a1"}, "a1"),
        ("signals", {"id": "s1", "sourceId": "src"}, "src"),
        ("usage", {"id": "u1", "month": "2026-11"}, "2026-11"),
    ],
)
def test_partition_key_comes_from_the_body(
    container: str, body: dict[str, Any], expected: str
) -> None:
    assert prepare_write(container, body).partition_key == expected


@pytest.mark.parametrize("value", [None, "", 7, ["a"], {"a": 1}, "x" * 1025])
def test_invalid_partition_key_values(value: object) -> None:
    with pytest.raises(InvalidRequest):
        prepare_write("article-versions", {"id": "v1", "articleId": value})
    with pytest.raises(InvalidRequest):
        prepare_write("article-versions", {"id": "v1"})


# --------------------------------------------------------------------------------------------
# Body normalisation
# --------------------------------------------------------------------------------------------


def test_prepare_write_returns_plain_json_and_compact_text() -> None:
    source: dict[str, Any] = {
        "id": Color.RED,
        "level": Level.HIGH,
        "nested": OrderedDict(b=(1, 2.5), a=[True, None]),
        "text": "Caf\N{LATIN SMALL LETTER E WITH ACUTE}",
    }
    prepared = prepare_write("jobs", source)
    assert prepared.id == "red"
    assert prepared.body == {
        "id": "red",
        "level": 3,
        "nested": {"b": [1, 2.5], "a": [True, None]},
        "text": "Caf\N{LATIN SMALL LETTER E WITH ACUTE}",
    }
    assert type(prepared.body["id"]) is str
    assert type(prepared.body["level"]) is int
    assert type(prepared.body["nested"]) is dict
    assert json.loads(prepared.text) == prepared.body
    assert " " not in prepared.text.replace("Caf", "")
    assert "\N{LATIN SMALL LETTER E WITH ACUTE}" in prepared.text


def test_prepare_write_copies_the_input() -> None:
    source: dict[str, Any] = {"id": "j1", "tags": ["a"]}
    prepared = prepare_write("jobs", source)
    source["tags"].append("b")
    assert prepared.body["tags"] == ["a"]


@pytest.mark.parametrize("body", [None, "id", ["id"], 3])
def test_body_must_be_a_mapping(body: object) -> None:
    with pytest.raises(InvalidRequest):
        prepare_write("jobs", body)


def test_problem_location_is_reported_without_values() -> None:
    with pytest.raises(InvalidRequest) as caught:
        prepare_write("jobs", {"id": "j1", "a": [1, {"b": {"secret"}}]})
    message = str(caught.value)
    assert "$.a[1].b" in message
    assert "set" in message
    assert "secret" not in message


def test_system_properties_are_listed() -> None:
    with pytest.raises(InvalidRequest, match="_etag, _ts"):
        prepare_write("jobs", {"id": "j1", "_ts": 1, "_etag": "x"})


@pytest.mark.parametrize("ttl", [-1, 1, 3600])
def test_valid_ttl(ttl: int) -> None:
    assert prepare_write("jobs", {"id": "j1", "ttl": ttl}).body["ttl"] == ttl


def test_size_limit_counts_utf8_bytes() -> None:
    overhead = len(prepare_write("jobs", {"id": "j1", "blob": ""}).text.encode())
    room = MAX_DOCUMENT_BYTES - overhead
    exact = prepare_write("jobs", {"id": "j1", "blob": "x" * room})
    assert len(exact.text.encode()) == MAX_DOCUMENT_BYTES
    with pytest.raises(InvalidRequest):
        prepare_write("jobs", {"id": "j1", "blob": "x" * (room + 1)})
    two_byte = "\N{LATIN SMALL LETTER E WITH ACUTE}"
    with pytest.raises(InvalidRequest):
        prepare_write("jobs", {"id": "j1", "blob": two_byte * (room // 2 + 1)})


# --------------------------------------------------------------------------------------------
# Stored documents
# --------------------------------------------------------------------------------------------


def test_doc_from_stored_strips_system_properties() -> None:
    stored = {
        "id": "v1",
        "articleId": "a1",
        "n": 1,
        "_rid": "r",
        "_self": "s",
        "_etag": '"e"',
        "_attachments": "a/",
        "_ts": 1,
    }
    doc = doc_from_stored("article-versions", stored, stored["_etag"])
    assert doc.id == "v1"
    assert doc.partition_key == "a1"
    assert doc.etag == '"e"'
    assert doc.body == {"id": "v1", "articleId": "a1", "n": 1}


@pytest.mark.parametrize(
    ("container", "stored", "etag"),
    [
        ("jobs", {"n": 1}, '"e"'),
        ("jobs", {"id": 5}, '"e"'),
        ("jobs", {"id": "j1"}, None),
        ("jobs", {"id": "j1"}, ""),
        ("article-versions", {"id": "v1"}, '"e"'),
        ("article-versions", {"id": "v1", "articleId": 3}, '"e"'),
    ],
)
def test_malformed_stored_documents_raise_store_error(
    container: str, stored: dict[str, Any], etag: object
) -> None:
    with pytest.raises(StoreError) as caught:
        doc_from_stored(container, stored, etag)
    assert type(caught.value) is StoreError


# --------------------------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------------------------


def test_error_statuses() -> None:
    assert NotFound("x").status == 404
    assert Conflict("x").status == 409
    assert PreconditionFailed("x").status == 412
    assert StoreUnavailable("x").status is None
    assert StoreUnavailable("x", status=429).status == 429
    assert StoreError("x", status=403).status == 403
    assert InvalidRequest("x").status is None


def test_error_hierarchy() -> None:
    for error in (NotFound, Conflict, PreconditionFailed, StoreUnavailable, InvalidRequest):
        assert issubclass(error, StoreError)
    assert issubclass(InvalidRequest, ValueError)
    assert not issubclass(NotFound, ValueError)
    assert str(NotFound("read jobs/x: not found")) == "read jobs/x: not found"


def test_container_check_failed_details() -> None:
    error = ContainerCheckFailed("bad", missing=["b", "a"], mismatched={"c": "/x"})
    assert error.missing == ("b", "a")
    assert error.mismatched == {"c": "/x"}
    assert ContainerCheckFailed("bad").missing == ()
    assert ContainerCheckFailed("bad").mismatched == {}
