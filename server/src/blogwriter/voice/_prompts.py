"""Versioned prompt files (``voice/prompts/<name>.v<N>.md``), loaded with importlib.resources.

The version a step used is recorded as ``promptVersion`` (``voice.<name>.v<N>``) on the
document the step wrote. Prompts never go to logs.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
from importlib import resources
from typing import Final, Literal

__all__ = ["PROMPT_VERSIONS", "Prompt", "PromptName", "load_prompt"]

type PromptName = Literal["guide", "digest"]

PROMPT_VERSIONS: Final[dict[PromptName, int]] = {"guide": 1, "digest": 1}
"""The prompt version each step uses (bump with a new ``.v<N>.md`` file; keep the old one)."""


@dataclass(frozen=True, slots=True)
class Prompt:
    text: str
    version: str
    """``voice.<name>.v<N>``, stored as ``promptVersion``."""


@functools.cache
def load_prompt(name: PromptName) -> Prompt:
    """The current version of prompt ``name``."""
    version = PROMPT_VERSIONS[name]
    path = resources.files("blogwriter.voice").joinpath(f"prompts/{name}.v{version}.md")
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise RuntimeError(f"prompt {name}.v{version} is empty")
    return Prompt(text=text, version=f"voice.{name}.v{version}")
