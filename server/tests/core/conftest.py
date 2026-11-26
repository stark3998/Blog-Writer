"""Fixtures for blogwriter.core tests."""

from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hide the developer's BW_* variables and the Container Apps marker from every test."""
    for name in list(os.environ):
        if name.startswith("BW_") or name == "CONTAINER_APP_NAME":
            monkeypatch.delenv(name)
