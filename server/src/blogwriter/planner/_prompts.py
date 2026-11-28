"""Versioned prompt files (``planner/prompts/<name>.v<N>.md``), loaded with importlib.resources.

A released prompt file never changes: a change is a new version, and the version a step used is
recorded as ``promptVersion`` (``planner.<name>.v<N>``) on what it produced. Prompts never go to
logs, job events or job outputs.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
from importlib import resources
from typing import Final, Literal

__all__ = ["PROMPT_VERSIONS", "Prompt", "PromptName", "load_prompt"]

type PromptName = Literal["plan"]

PROMPT_VERSIONS: Final[dict[PromptName, int]] = {"plan": 1}
"""The prompt version each step uses (bump with a new ``.v<N>.md`` file; keep the old one)."""


@dataclass(frozen=True, slots=True)
class Prompt:
    text: str
    version: str
    """``planner.<name>.v<N>``, stored as ``promptVersion``."""


@functools.cache
def load_prompt(name: PromptName) -> Prompt:
    """The current version of prompt ``name``."""
    version = PROMPT_VERSIONS[name]
    path = resources.files("blogwriter.planner").joinpath(f"prompts/{name}.v{version}.md")
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise RuntimeError(f"prompt {name}.v{version} is empty")
    return Prompt(text=text, version=f"planner.{name}.v{version}")
