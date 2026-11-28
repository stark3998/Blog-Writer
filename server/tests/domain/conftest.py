"""Fixtures for the domain tests: fixed time and no services left installed between tests."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest

from blogwriter.domain.ports import uninstall_services

T0 = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def no_installed_services() -> Iterator[None]:
    uninstall_services()
    yield
    uninstall_services()
