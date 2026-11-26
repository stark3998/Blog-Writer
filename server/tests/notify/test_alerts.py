"""Tests for blogwriter.notify.alerts: the out-of-band alert event."""

from __future__ import annotations

import json
import logging
import uuid
from typing import cast, get_args

import pytest

from blogwriter.core.logging import JsonFormatter, allowed_log_fields
from blogwriter.notify import ALERT_EVENT, ALERT_KINDS, AlertKind, emit_alert


def alert_lines(caplog: pytest.LogCaptureFixture) -> list[dict[str, object]]:
    formatter = JsonFormatter()
    lines: list[dict[str, object]] = [json.loads(formatter.format(r)) for r in caplog.records]
    return [line for line in lines if line["event"] == ALERT_EVENT]


def test_one_allowlisted_line(caplog: pytest.LogCaptureFixture) -> None:
    emit_alert("job_failed", "job-01JD3XQ7ZK")
    (line,) = alert_lines(caplog)
    assert set(line) == {"ts", "level", "logger", "event", "alert_kind", "ref_id"}
    assert line["event"] == "alert_event"
    assert line["level"] == "ERROR"
    assert line["logger"] == "blogwriter.notify.alerts"
    assert line["alert_kind"] == "job_failed"
    assert line["ref_id"] == "job-01JD3XQ7ZK"


def test_exactly_one_record_per_call(caplog: pytest.LogCaptureFixture) -> None:
    emit_alert("test")
    emit_alert("brake_tripped", "ledger:2026-11")
    assert len(caplog.records) == 2
    assert [line["alert_kind"] for line in alert_lines(caplog)] == ["test", "brake_tripped"]


def test_ref_id_is_optional(caplog: pytest.LogCaptureFixture) -> None:
    emit_alert("test")
    (line,) = alert_lines(caplog)
    assert "ref_id" not in line
    assert line["alert_kind"] == "test"


@pytest.mark.parametrize(
    "ref_id",
    [
        "",
        "Post title with spaces",
        "https://example.com/draft?id=1",
        "a" * 129,
        "-leading-dash",
        "id\nnewline",
        "naïve",
    ],
)
def test_content_is_never_logged_as_ref_id(caplog: pytest.LogCaptureFixture, ref_id: str) -> None:
    emit_alert("publish_failed", ref_id)
    (line,) = alert_lines(caplog)
    assert "ref_id" not in line
    assert line["reason"] == "ref_id_rejected"
    assert line["alert_kind"] == "publish_failed"  # the alert still fires
    if ref_id:
        assert ref_id not in json.dumps(line)


@pytest.mark.parametrize("ref_id", ["a", "job-1", "post:2026-11-26.1", "A" * 128, "f" * 32])
def test_id_shaped_ref_ids_are_kept(caplog: pytest.LogCaptureFixture, ref_id: str) -> None:
    emit_alert("needs_attention", ref_id)
    (line,) = alert_lines(caplog)
    assert line["ref_id"] == ref_id


def test_unknown_kinds_still_alert(caplog: pytest.LogCaptureFixture) -> None:
    emit_alert(cast(AlertKind, "Something odd happened"))
    (line,) = alert_lines(caplog)
    assert line["alert_kind"] == "unknown"


@pytest.mark.parametrize(
    ("kind", "ref_id"),
    [
        ("job_failed", uuid.UUID("12345678-1234-5678-1234-567812345678")),
        ("job_failed", 42),
        ("job_failed", b"job-1"),
        (["job_failed"], "job-1"),  # unhashable
        (None, None),
    ],
)
def test_wrongly_typed_arguments_never_raise(
    caplog: pytest.LogCaptureFixture, kind: object, ref_id: object
) -> None:
    # emit_alert runs inside except blocks, where a TypeError would hide the real failure.
    emit_alert(cast(AlertKind, kind), cast(str | None, ref_id))
    (line,) = alert_lines(caplog)
    assert line["alert_kind"] == ("job_failed" if kind == "job_failed" else "unknown")
    if isinstance(ref_id, str):
        assert line["ref_id"] == ref_id
    else:
        assert "ref_id" not in line
        assert line.get("reason") == (None if ref_id is None else "ref_id_rejected")


def test_kinds_cover_the_plan() -> None:
    assert frozenset(get_args(AlertKind.__value__)) == ALERT_KINDS
    assert {
        "job_failed",
        "publish_failed",
        "needs_attention",
        "quarantined",
        "brake_tripped",
        "notification_failed",
        "test",
    } == ALERT_KINDS


def test_fields_are_registered() -> None:
    assert {"alert_kind", "ref_id", "reason"} <= allowed_log_fields()


def test_alerts_pass_a_warning_level_filter(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING)
    emit_alert("quarantined", "run-7")
    assert len(alert_lines(caplog)) == 1
