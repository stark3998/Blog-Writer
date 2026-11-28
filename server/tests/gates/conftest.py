"""Fixtures for the gates tests: no developer ``BW_GATES_*`` settings and no services left
installed between tests. Shared builders live in :mod:`blogwriter.gates.testing`."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from blogwriter.domain.ports import uninstall_services
from blogwriter.gates.settings import gates_settings

GOLDENS = Path(__file__).resolve().parent / "goldens"


@pytest.fixture(autouse=True)
def isolated(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for name in list(os.environ):
        if name.startswith("BW_GATES_"):
            monkeypatch.delenv(name)
    gates_settings.cache_clear()
    uninstall_services()
    yield
    uninstall_services()
    gates_settings.cache_clear()


@pytest.fixture
def goldens() -> Path:
    return GOLDENS
