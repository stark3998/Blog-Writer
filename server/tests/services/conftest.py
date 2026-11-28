"""Fixtures for the blogwriter.services tests: an isolated environment and no installed services.

Typed helpers (a recording web push function, VAPID and browser keys) live in
:mod:`blogwriter.services.testing`.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

from blogwriter.domain.ports import uninstall_services


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Hide the developer's BW_* variables and leave no services installed between tests."""
    for name in list(os.environ):
        if name.startswith("BW_") or name == "CONTAINER_APP_NAME":
            monkeypatch.delenv(name)
    uninstall_services()
    yield
    uninstall_services()
