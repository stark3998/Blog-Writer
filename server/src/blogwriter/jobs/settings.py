"""Job runner, scheduler, broker and SSE settings (env prefix ``BW_JOBS_``).

Every field reads ``BW_JOBS_<FIELD>`` (for example ``BW_JOBS_LEASE_SECONDS``). Defaults are the
production values from the plan ("Reliability model"): a 60 s lease renewed every 15 s with a
5 s clock-skew allowance, at most 3 attempts per job, a 60 s scheduler tick, a 15 s SSE
heartbeat and streams the server closes after 200 s (before the 240 s platform limit).

Durations are floats so tests can shrink them; the validators keep the relations that make
claims safe (a renewal always lands well before the lease could be taken over).
"""

from __future__ import annotations

import os
import re
import socket
from typing import Final, Self

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ["JobsSettings", "replica_name"]

#: Environment variable Azure Container Apps sets to the replica's name.
ACA_REPLICA_ENV: Final = "CONTAINER_APP_REPLICA_NAME"

_REPLICA_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")
_MAX_REPLICA_CHARS: Final = 100


class JobsSettings(BaseSettings):
    """Settings for ``blogwriter.jobs``. Defaults are safe for local tests and right for prod."""

    model_config = SettingsConfigDict(
        env_prefix="BW_JOBS_", env_ignore_empty=True, extra="forbid", frozen=True
    )

    # Claims ---------------------------------------------------------------------------------
    lease_seconds: float = Field(default=60.0, gt=0, le=3600)
    """How long a claim stays valid without renewal."""
    renew_seconds: float = Field(default=15.0, gt=0, le=3600)
    """How often the holder renews its claim."""
    skew_seconds: float = Field(default=5.0, ge=0, le=600)
    """Clock-skew allowance: a lease is taken over only once it is this far past expiry, and the
    holder treats it as lost this long before expiry."""
    store_timeout_seconds: float = Field(default=10.0, gt=0, le=120)
    """Upper bound for one store call made by the runner or the scheduler."""

    # Runner ---------------------------------------------------------------------------------
    concurrency: int = Field(default=4, ge=1, le=64)
    """Jobs one runner executes at the same time."""
    poll_seconds: float = Field(default=5.0, gt=0, le=300)
    """How often the claim loop queries the store when nothing wakes it earlier."""
    max_attempts: int = Field(default=3, ge=1, le=20)
    """Attempts per job (default for new jobs); then the job is quarantined."""
    retry_base_seconds: float = Field(default=30.0, ge=0, le=86_400)
    """Backoff before the second attempt; doubles per attempt."""
    retry_max_seconds: float = Field(default=900.0, ge=0, le=86_400)
    """Cap of the retry backoff."""
    shutdown_grace_seconds: float = Field(default=2.0, ge=0, le=60)
    """On shutdown, how long running steps may finish before they are cancelled and released."""

    # Crash-loop guard -----------------------------------------------------------------------
    crash_loop_max_boots: int = Field(default=5, ge=1, le=1000)
    """More boots than this inside the window start the app in safe mode (nothing runs)."""
    crash_loop_window_seconds: float = Field(default=3600.0, gt=0, le=7 * 86_400)

    # Scheduler ------------------------------------------------------------------------------
    tick_seconds: float = Field(default=60.0, gt=0, le=3600)
    """Interval of the scheduler tick."""

    # Broker and SSE -------------------------------------------------------------------------
    broker_queue_size: int = Field(default=256, ge=8, le=100_000)
    """Items one subscriber may have queued (token deltas are dropped oldest-first beyond it)."""
    broker_max_subscribers: int = Field(default=16, ge=1, le=1000)
    sse_heartbeat_seconds: float = Field(default=15.0, gt=0, le=120)
    sse_max_stream_seconds: float = Field(default=200.0, gt=0, le=235)
    """The server closes every stream after this long (the platform cuts them at 240 s)."""
    sse_retry_ms: int = Field(default=2000, ge=0, le=60_000)
    """Reconnect delay suggested to ``EventSource`` (``retry:`` field)."""
    sse_send_timeout_seconds: float = Field(default=10.0, gt=0, le=120)
    """A client that does not take a chunk within this time is disconnected."""
    sse_recent_seconds: float = Field(default=900.0, ge=0, le=86_400)
    """App-wide snapshots include jobs updated this recently, besides every active job."""
    sse_snapshot_limit: int = Field(default=50, ge=1, le=500)
    """Most jobs one snapshot covers."""

    # Identity -------------------------------------------------------------------------------
    replica_name: str | None = None
    """Name used in claim owners. Default: ``CONTAINER_APP_REPLICA_NAME``, else the hostname."""

    @field_validator("replica_name")
    @classmethod
    def _check_replica_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned or _REPLICA_UNSAFE.search(cleaned) or len(cleaned) > _MAX_REPLICA_CHARS:
            raise ValueError("must be 1-100 characters of [A-Za-z0-9._-]")
        return cleaned

    @model_validator(mode="after")
    def _check_relations(self) -> Self:
        if self.skew_seconds * 2 >= self.lease_seconds:
            raise ValueError("skew_seconds must be less than half of lease_seconds")
        if self.renew_seconds >= self.lease_seconds - self.skew_seconds:
            raise ValueError("renew_seconds must be shorter than lease_seconds - skew_seconds")
        if self.retry_max_seconds < self.retry_base_seconds:
            raise ValueError("retry_max_seconds must be at least retry_base_seconds")
        if self.sse_heartbeat_seconds >= self.sse_max_stream_seconds:
            raise ValueError("sse_heartbeat_seconds must be shorter than sse_max_stream_seconds")
        return self


def replica_name(settings: JobsSettings) -> str:
    """The replica part of claim owners: setting, then the ACA replica name, then the hostname.

    Characters outside ``[A-Za-z0-9._-]`` are replaced so the value is safe in ids and logs.
    """
    candidates = (settings.replica_name, os.environ.get(ACA_REPLICA_ENV), socket.gethostname())
    for candidate in candidates:
        if candidate and candidate.strip():
            cleaned = _REPLICA_UNSAFE.sub("-", candidate.strip())[:_MAX_REPLICA_CHARS]
            if cleaned.strip("-"):
                return cleaned
    return "replica"
