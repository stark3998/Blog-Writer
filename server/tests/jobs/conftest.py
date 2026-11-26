"""Fixtures for the jobs tests: a MemoryStore, a fake clock and an isolated environment."""

from __future__ import annotations

import os
from datetime import UTC, datetime

import pytest

from blogwriter.db import MemoryStore
from blogwriter.jobs.testing import FakeClock

T0 = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hide the developer's BW_JOBS_* variables and the ACA replica name from every test."""
    for name in list(os.environ):
        if name.startswith("BW_JOBS_") or name == "CONTAINER_APP_REPLICA_NAME":
            monkeypatch.delenv(name)


@pytest.fixture
def store() -> MemoryStore:
    return MemoryStore()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(T0)
