"""The research prompts: versioned package files (``prompts/<name>.v<N>.md``).

Each AI step records the version it used (``ServedByRecord.prompt_version``), so a brief can be
traced to the exact instructions that produced it. Prompts are static system prompts; the run's
data (topic, sub-questions, passages, claims) goes into the user message, inside delimited
sections that the prompt tells the model to treat as data.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
from importlib import resources
from typing import Final, Literal

__all__ = ["PROMPT_FILES", "Prompt", "PromptName", "load_prompt"]

type PromptName = Literal["plan", "search", "extract", "verify", "brief", "quick"]

PROMPT_FILES: Final[dict[PromptName, str]] = {
    "plan": "plan.v1",
    "search": "search.v1",
    "extract": "extract.v1",
    "verify": "verify.v1",
    "brief": "brief.v1",
    "quick": "quick.v1",
}
"""The current version of each prompt. Bump the file name (never edit a released version)."""


@dataclass(frozen=True, slots=True)
class Prompt:
    version: str
    """For example ``extract.v1`` (stored as ``promptVersion``)."""
    text: str


@functools.cache
def load_prompt(name: PromptName) -> Prompt:
    """The current prompt ``name`` (read once from the package data)."""
    version = PROMPT_FILES[name]
    path = resources.files("blogwriter.research").joinpath(f"prompts/{version}.md")
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise RuntimeError(f"prompt {version} is empty")
    return Prompt(version=version, text=text)
