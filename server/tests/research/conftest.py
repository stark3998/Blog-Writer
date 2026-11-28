"""Fixtures for the research tests: default settings, fast child delivery, clean services.

Builders live in :mod:`blogwriter.research.testing` (test modules cannot import this file).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta

import pytest

from blogwriter.domain.ports import uninstall_services
from blogwriter.research import run as run_module
from blogwriter.research.prompting import load_prompt
from blogwriter.research.run import ChildDelivery
from blogwriter.research.settings import research_settings

_SETTINGS = (
    "WEBIQ_ENDPOINT",
    "WEBIQ_SCOPE",
    "WEBIQ_QPS",
    "MAX_SOURCES",
    "MAX_TOOL_CALLS",
    "FETCH_TIMEOUT_SECONDS",
    "PASSAGE_MAX_CHARS",
    "QUICK_TIMEOUT_SECONDS",
)


@pytest.fixture(autouse=True)
def research_environment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Default settings (no ``BW_RESEARCH_*`` from the shell), a short child-delivery window
    and no services left installed between tests."""
    for name in _SETTINGS:
        monkeypatch.delenv(f"BW_RESEARCH_{name}", raising=False)
    research_settings.cache_clear()
    load_prompt.cache_clear()
    monkeypatch.setattr(
        run_module,
        "CHILD_DELIVERY",
        ChildDelivery(window=timedelta(milliseconds=300), interval=timedelta(milliseconds=20)),
    )
    uninstall_services()
    yield
    uninstall_services()
    research_settings.cache_clear()
