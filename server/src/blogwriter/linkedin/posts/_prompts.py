"""Versioned prompt files (``posts/prompts/<name>.v<N>.md``) loaded with importlib.resources.

The version a step used is recorded as ``promptVersion`` (``posts.<name>.v<N>``) in the
``servedBy`` record of the post it wrote. Prompts never go to logs, events or job documents.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
from importlib import resources
from typing import Final, Literal

__all__ = ["PROMPT_VERSIONS", "Prompt", "PromptName", "load_prompt"]

type PromptName = Literal[
    "system", "derive", "native", "quick_take", "repurpose", "regenerate", "fact_check"
]

PROMPT_VERSIONS: Final[dict[PromptName, int]] = {
    "system": 1,
    "derive": 1,
    "native": 1,
    "quick_take": 1,
    "repurpose": 1,
    "regenerate": 1,
    "fact_check": 1,
}
"""The prompt version each step uses (bump with a new ``.v<N>.md`` file; keep the old one)."""


@dataclass(frozen=True, slots=True)
class Prompt:
    text: str
    version: str
    """``posts.<name>.v<N>``, stored as ``promptVersion``."""


@functools.cache
def load_prompt(name: PromptName) -> Prompt:
    """The current version of prompt ``name``."""
    version = PROMPT_VERSIONS[name]
    path = resources.files("blogwriter.linkedin.posts").joinpath(f"prompts/{name}.v{version}.md")
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise RuntimeError(f"prompt {name}.v{version} is empty")
    return Prompt(text=text, version=f"posts.{name}.v{version}")
