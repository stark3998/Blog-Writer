"""The job document: wire format, events cap, validation and the timestamp format."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from blogwriter.jobs.clock import is_timestamp, parse_iso, to_iso
from blogwriter.jobs.models import (
    MAX_EVENT_DATA_BYTES,
    MAX_EVENTS,
    Job,
    StepState,
    check_json,
    new_job_id,
)

STAMP = "2026-11-26T12:00:00.000000Z"


def make_job(**overrides: object) -> Job:
    values: dict[str, object] = {
        "id": "job-1",
        "type": "demo",
        "run_after": STAMP,
        "created_at": STAMP,
        "updated_at": STAMP,
    }
    values.update(overrides)
    return Job.model_validate(values)


def test_the_document_is_camel_case_and_round_trips() -> None:
    job = make_job(
        steps=[StepState(name="draft", remote_ids={"response": "resp-1"})],
        lease_until=STAMP,
        cancel_requested=True,
    )
    body = job.to_body()
    assert body["kind"] == "job"
    assert body["leaseUntil"] == STAMP
    assert body["maxAttempts"] == 3
    assert body["runAfter"] == STAMP
    assert body["cancelRequested"] is True
    assert body["steps"][0]["remoteIds"] == {"response": "resp-1"}
    assert "ttl" not in body  # unset TTL stays out of the document (Cosmos rejects null)
    assert Job.from_body(body) == job


def test_unknown_fields_survive_a_round_trip() -> None:
    body = make_job().to_body()
    body["addedByANewerRevision"] = {"x": 1}
    body["steps"] = [{"name": "a", "futureField": True}]
    again = Job.from_body(body).to_body()
    assert again["addedByANewerRevision"] == {"x": 1}
    assert again["steps"][0]["futureField"] is True


def test_ttl_is_written_when_set() -> None:
    assert make_job(ttl=3600).to_body()["ttl"] == 3600
    with pytest.raises(ValidationError):
        make_job(ttl=0)


def test_events_are_capped_and_seq_keeps_counting() -> None:
    job = make_job()
    for number in range(MAX_EVENTS + 10):
        job.add_event("progress", at=STAMP, data={"n": number})
    assert len(job.events) == MAX_EVENTS
    assert job.seq == MAX_EVENTS + 10
    assert [event.seq for event in job.events] == list(range(11, MAX_EVENTS + 11))
    assert [event.seq for event in job.events_after(57)] == [58, 59, 60]
    assert Job.from_body(job.to_body()).seq == MAX_EVENTS + 10


def test_event_data_has_a_size_limit() -> None:
    job = make_job()
    with pytest.raises(ValueError, match="larger than"):
        job.add_event("progress", at=STAMP, data={"blob": "x" * MAX_EVENT_DATA_BYTES})
    assert job.seq == 0
    assert job.events == []


@pytest.mark.parametrize(
    "overrides",
    [
        {"id": "bad/id"},
        {"id": " padded"},
        {"type": "Has Spaces"},
        {"status": "paused"},
        {"run_after": "2026-11-26T12:00:00Z"},  # not the fixed-width format
        {"lease_until": "yesterday"},
        {"attempt": -1},
        {"max_attempts": 0},
        {"steps": [{"name": "a"}, {"name": "a"}]},
        {"steps": [{"name": "a", "status": "done"}]},
        {"seq": 0, "events": [{"seq": 1, "at": STAMP, "type": "status"}]},
    ],
)
def test_invalid_documents_are_rejected(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        make_job(**overrides)


def test_step_lookup_and_flags() -> None:
    job = make_job(steps=[StepState(name="a"), StepState(name="b")])
    assert job.step("b") is job.steps[1]
    assert job.step("c") is None
    assert job.is_active
    job.status = "succeeded"
    assert job.is_terminal
    assert not job.is_active


def test_new_job_ids_are_unique_and_valid() -> None:
    first, second = new_job_id(), new_job_id()
    assert first != second
    assert make_job(id=first).id == first


def test_check_json_accepts_json_and_rejects_the_rest() -> None:
    value = {"a": [1, 2.5, "x", None, True], "b": {"c": False}}
    assert check_json(value, what="v") == value
    for bad in ({1, 2}, (1, 2), b"bytes", object(), {1: "non-string key"}):
        with pytest.raises(ValueError, match="must be JSON"):
            check_json(bad, what="v")


def test_timestamps_are_fixed_width_utc() -> None:
    moment = datetime(2026, 3, 8, 7, 30, tzinfo=UTC)
    assert to_iso(moment) == "2026-03-08T07:30:00.000000Z"
    eastern = moment.astimezone(timezone(timedelta(hours=-4)))
    assert to_iso(eastern) == "2026-03-08T07:30:00.000000Z"
    assert parse_iso(to_iso(moment)) == moment
    with pytest.raises(ValueError, match="aware"):
        to_iso(datetime(2026, 1, 1))
    malformed = (
        "2026-03-08T07:30:00Z",
        "2026-03-08 07:30:00.000000Z",
        "2026-03-08T07:30:00.000000+00:00",
    )
    for bad in malformed:
        with pytest.raises(ValueError, match="must look like"):
            parse_iso(bad)
        assert not is_timestamp(bad)
    with pytest.raises(ValueError, match="month"):
        parse_iso("2026-13-01T00:00:00.000000Z")
    assert not is_timestamp("2026-13-01T00:00:00.000000Z")
    assert not is_timestamp(None)


def test_string_order_of_timestamps_is_time_order() -> None:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    moments = [base + timedelta(microseconds=n * 999_983) for n in range(0, 5000, 37)]
    texts = [to_iso(moment) for moment in moments]
    assert texts == sorted(texts)
