"""``BW_JOBS_*`` settings: defaults, the relations that keep claims safe, the replica name."""

from __future__ import annotations

import socket

import pytest
from pydantic import ValidationError

from blogwriter.jobs.settings import JobsSettings, replica_name


def test_defaults_are_the_plan_values() -> None:
    settings = JobsSettings()
    assert (settings.lease_seconds, settings.renew_seconds, settings.skew_seconds) == (60, 15, 5)
    assert settings.max_attempts == 3
    assert settings.tick_seconds == 60
    assert settings.sse_heartbeat_seconds == 15
    assert settings.sse_max_stream_seconds == 200


def test_environment_variables_use_the_bw_jobs_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BW_JOBS_CONCURRENCY", "7")
    monkeypatch.setenv("BW_JOBS_SSE_MAX_STREAM_SECONDS", "120")
    settings = JobsSettings()
    assert settings.concurrency == 7
    assert settings.sse_max_stream_seconds == 120


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"renew_seconds": 55}, "renew_seconds"),
        ({"skew_seconds": 30}, "skew_seconds"),
        ({"retry_base_seconds": 60, "retry_max_seconds": 30}, "retry_max_seconds"),
        ({"sse_heartbeat_seconds": 30, "sse_max_stream_seconds": 20}, "sse_heartbeat_seconds"),
        ({"sse_max_stream_seconds": 240}, "less than or equal"),
        ({"concurrency": 0}, "greater than or equal"),
        ({"replica_name": "bad name!"}, "A-Za-z0-9"),
        ({"unknown_setting": 1}, "Extra inputs"),
    ],
)
def test_unsafe_combinations_are_rejected(overrides: dict[str, object], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        JobsSettings.model_validate(overrides)


def test_replica_name_prefers_setting_then_aca_then_hostname(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert replica_name(JobsSettings(replica_name="api-7")) == "api-7"
    monkeypatch.setenv("CONTAINER_APP_REPLICA_NAME", "ca-blogwriter--rev1-abc")
    assert replica_name(JobsSettings()) == "ca-blogwriter--rev1-abc"
    monkeypatch.setenv("CONTAINER_APP_REPLICA_NAME", "odd name/with:chars")
    assert replica_name(JobsSettings()) == "odd-name-with-chars"
    monkeypatch.delenv("CONTAINER_APP_REPLICA_NAME")
    monkeypatch.setattr(socket, "gethostname", lambda: "")
    assert replica_name(JobsSettings()) == "replica"
