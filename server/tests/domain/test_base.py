"""Timestamps, ids, field types and the Document base."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from typing import ClassVar

import pytest
from pydantic import Field, ValidationError

from blogwriter.ai.ledger import UsageRecord
from blogwriter.ai.types import Task, Usage
from blogwriter.db.base import validate_id
from blogwriter.domain.base import (
    WEEKDAYS,
    Document,
    DomainModel,
    ExpiringDocument,
    SingletonDocument,
    WrongKind,
    canonical_json,
    check_https_url,
    is_timestamp,
    new_id,
    parse_iso,
    sha256_hex,
    to_iso,
)
from blogwriter.jobs import clock as jobs_clock
from blogwriter.jobs.scheduler import WEEKDAYS as JOBS_WEEKDAYS


class Widget(Document):
    CONTAINER: ClassVar[str] = "ideas"
    KIND: ClassVar[str] = "widget"
    ID_PREFIX: ClassVar[str] = "wid"

    label: str = Field(max_length=20)
    status_changed_at: str | None = None


class Note(ExpiringDocument):
    CONTAINER: ClassVar[str] = "engagement"
    KIND: ClassVar[str] = "note"


class Prefs(SingletonDocument):
    KIND: ClassVar[str] = "prefs"
    SINGLETON_ID: ClassVar[str] = "prefs"

    colour: str = "blue"


NOW = "2026-11-26T12:00:00.000000Z"


def widget(**extra: object) -> Widget:
    return Widget.model_validate(
        {"id": "wid-1", "createdAt": NOW, "updatedAt": NOW, "label": "x", **extra}
    )


# ---------------------------------------------------------------------------------------------
# Timestamps
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "moment",
    [
        datetime(2026, 11, 26, 12, 0, tzinfo=UTC),
        datetime(2026, 3, 8, 2, 30, 15, 123456, tzinfo=timezone(timedelta(hours=-5))),
        datetime(1999, 12, 31, 23, 59, 59, 999999, tzinfo=UTC),
    ],
)
def test_timestamps_match_jobs_and_ledger_format(moment: datetime) -> None:
    text = to_iso(moment)
    assert text == jobs_clock.to_iso(moment)
    assert len(text) == 27
    assert parse_iso(text) == moment
    record = UsageRecord(
        task=Task.DRAFT,
        model="m",
        deployment="d",
        provider="openai",
        usage=Usage(),
        cost_usd=Decimal(0),
        at=moment,
        origin="owner",
        outcome="completed",
    )
    assert record.to_document()["at"] == text


def test_timestamps_refuse_naive_and_other_formats() -> None:
    with pytest.raises(ValueError, match="aware"):
        to_iso(datetime(2026, 1, 1))
    for bad in (
        "2026-11-26T12:00:00Z",
        "2026-11-26 12:00:00.000000Z",
        "2026-02-30T00:00:00.000000Z",
    ):
        assert not is_timestamp(bad)
        with pytest.raises(ValueError, match=r"timestamp|day|month"):
            parse_iso(bad)
    assert not is_timestamp(5)


def test_string_order_is_time_order() -> None:
    earlier = to_iso(datetime(2026, 1, 1, 9, tzinfo=UTC))
    later = to_iso(datetime(2026, 1, 1, 10, tzinfo=UTC))
    assert earlier < later


def test_weekdays_match_the_scheduler() -> None:
    assert WEEKDAYS == JOBS_WEEKDAYS


# ---------------------------------------------------------------------------------------------
# Ids, hashes, URLs
# ---------------------------------------------------------------------------------------------


def test_new_id_shape_and_order() -> None:
    first = new_id("art", now=datetime(2026, 11, 26, tzinfo=UTC))
    second = new_id("art", now=datetime(2026, 11, 27, tzinfo=UTC))
    assert validate_id(first) == first
    assert first.startswith("art-")
    assert len(first) == 4 + 28
    assert first < second
    assert new_id("art") != new_id("art")


@pytest.mark.parametrize("prefix", ["", "Art", "1x", "toolongprefixx", "a-b"])
def test_new_id_refuses_bad_prefixes(prefix: str) -> None:
    with pytest.raises(ValueError, match="prefix"):
        new_id(prefix)


def test_new_id_refuses_naive_times() -> None:
    with pytest.raises(ValueError, match="aware"):
        new_id("art", now=datetime(2026, 1, 1))


def test_canonical_json_and_sha256() -> None:
    assert canonical_json({"b": 1, "a": "é"}) == '{"a":"é","b":1}'.encode()
    assert sha256_hex("abc") == sha256_hex(b"abc")
    assert sha256_hex("abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    with pytest.raises(ValueError, match="JSON compliant"):
        canonical_json({"x": float("nan")})


@pytest.mark.parametrize(
    "url",
    ["https://learn.microsoft.com/a?b=c#d", "https://example.com"],
)
def test_https_urls_accepted(url: str) -> None:
    assert check_https_url(url) == url


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com",
        "https://user:pw@example.com/",
        "https:///nohost",
        "https://example.com/a b",
        "https://example.com/\\x",
        "javascript:alert(1)",
        "https://example.com/" + "a" * 2100,
    ],
)
def test_bad_urls_refused(url: str) -> None:
    with pytest.raises(ValueError, match="URL"):
        check_https_url(url)


# ---------------------------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------------------------


def test_camel_case_on_the_wire_and_snake_case_accepted() -> None:
    class Part(DomainModel):
        status_changed_at: str

    assert Part(status_changed_at="x").model_dump() == {"statusChangedAt": "x"}
    assert Part.model_validate({"status_changed_at": "y"}).status_changed_at == "y"


def test_document_body_has_id_and_kind_first() -> None:
    body = widget().to_document()
    assert list(body)[:2] == ["id", "kind"]
    assert body["kind"] == "widget"
    assert body["createdAt"] == NOW
    assert body["schemaVersion"] == 1
    assert Widget.from_document(body) == widget()


def test_unknown_fields_survive_a_round_trip() -> None:
    body = {**widget().to_document(), "addedByNewerRevision": {"x": [1, 2]}}
    again = Widget.from_document(body).to_document()
    assert again["addedByNewerRevision"] == {"x": [1, 2]}
    assert json.loads(json.dumps(again)) == again


def test_from_document_requires_the_right_kind() -> None:
    body = widget().to_document()
    with pytest.raises(WrongKind):
        Widget.from_document({**body, "kind": "other"})
    with pytest.raises(WrongKind):
        Widget.from_document({key: value for key, value in body.items() if key != "kind"})
    with pytest.raises(ValidationError):
        Widget.model_validate({**body, "kind": "other"})


def test_model_validate_drops_a_matching_kind() -> None:
    parsed = Widget.model_validate(widget().to_document())
    assert parsed.model_extra == {}


def test_document_fields_are_validated() -> None:
    with pytest.raises(ValidationError):
        widget(id="bad/id")
    with pytest.raises(ValidationError):
        widget(createdAt="yesterday")
    with pytest.raises(ValidationError):
        widget(label="x" * 21)


def test_new_id_and_partition_key() -> None:
    assert Widget.new_id().startswith("wid-")
    assert widget().partition_key == "wid-1"
    assert Widget.partition_path() == "/id"
    with pytest.raises(TypeError, match="deterministic"):
        Prefs.new_id()


def test_touched_updates_only_updated_at() -> None:
    later = "2026-11-27T12:00:00.000000Z"
    touched = widget().touched(later)
    assert touched.updated_at == later
    assert touched.created_at == NOW
    with pytest.raises(ValueError, match="timestamp"):
        widget().touched("now")


def test_expiring_document_ttl_and_expiry() -> None:
    note = Note(id="n-1", created_at=NOW, updated_at=NOW, expires_at="2026-11-28T12:00:00.000000Z")
    body = note.to_document()
    assert "ttl" not in body
    assert not note.expired(datetime(2026, 11, 27, tzinfo=UTC))
    assert note.expired(datetime(2026, 11, 28, 12, tzinfo=UTC))
    with_ttl = note.model_copy(update={"ttl": 3600}).to_document()
    assert with_ttl["ttl"] == 3600
    for bad in (0, -2):
        with pytest.raises(ValidationError):
            Note(id="n", created_at=NOW, updated_at=NOW, expires_at=NOW, ttl=bad)


def test_singleton_default_and_fixed_id() -> None:
    prefs = Prefs.default(NOW)
    assert prefs.id == "prefs"
    assert prefs.colour == "blue"
    assert prefs.to_document()["kind"] == "prefs"
    with pytest.raises(ValidationError):
        Prefs(id="other", created_at=NOW, updated_at=NOW)
