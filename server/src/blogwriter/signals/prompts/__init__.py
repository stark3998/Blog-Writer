"""Versioned prompts of the signals package (``<name>.v<N>.md``, loaded as package data).

A prompt file never changes once released: a change is a new version, and the version is
recorded on every document it produced (``ServedByRecord.prompt_version``).
"""

from __future__ import annotations

import functools
from importlib import resources
from typing import Final

__all__ = ["SCOUT_PROMPT", "TRIAGE_PROMPT", "load_prompt"]

_PACKAGE: Final = "blogwriter.signals.prompts"
TRIAGE_PROMPT: Final = "triage.v1"
SCOUT_PROMPT: Final = "scout.v1"


@functools.cache
def load_prompt(version: str) -> str:
    """The text of ``prompts/<version>.md`` (``FileNotFoundError`` for an unknown version)."""
    return resources.files(_PACKAGE).joinpath(f"{version}.md").read_text(encoding="utf-8").strip()
