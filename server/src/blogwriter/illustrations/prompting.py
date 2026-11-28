"""Versioned prompt files (``prompts/<name>.v<N>.md``) and data fences of the illustrations.

A prompt's version is its file name without ``.md`` (``plan.v1``) and is recorded as
``promptVersion`` on the ``servedBy`` records of the calls it made. Change a prompt only by
adding a new version file and pointing the constant at it.

Text that reaches a model goes inside delimited sections (:func:`fenced`) that the system prompt
declares to be data; a copy of the closing tag inside the data is defused.
"""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass, field
from importlib import resources
from typing import Final

__all__ = ["CHECK", "PLAN", "PROMPT_NAMES", "Prompt", "fenced", "load_prompt"]

PLAN: Final = "plan.v1"
"""The illustration plan (``Task.PLAN``, public data)."""
CHECK: Final = "check.v1"
"""The "media matches text" vision check (``Task.FACT_CHECK``, public data and the image)."""
PROMPT_NAMES: Final = (PLAN, CHECK)

_PACKAGE: Final = "blogwriter.illustrations"
_NAME_RE: Final = re.compile(r"[a-z][a-z0-9-]*\.v[0-9]+")
_TAG_RE: Final = re.compile(r"[a-z][a-z_]*")


@dataclass(frozen=True, slots=True)
class Prompt:
    """One prompt file: its version (the file stem) and its text."""

    version: str
    text: str = field(repr=False)


@functools.cache
def load_prompt(name: str) -> Prompt:
    """The prompt ``prompts/<name>.md`` (``name`` like ``plan.v1``)."""
    if not _NAME_RE.fullmatch(name):
        raise ValueError("prompt names look like plan.v1")
    text = resources.files(_PACKAGE).joinpath(f"prompts/{name}.md").read_text(encoding="utf-8")
    body = text.strip()
    if not body:
        raise ValueError(f"prompt {name} is empty")
    return Prompt(version=name, text=body)


def fenced(tag: str, text: str) -> str:
    """``text`` inside ``<tag>`` and ``</tag>``; any copy of the tag inside ``text`` is defused
    (``<`` becomes U+2039), so the data cannot close its section early."""
    if not _TAG_RE.fullmatch(tag):
        raise ValueError("tags are lowercase words")
    pattern = re.compile(rf"<(/?\s*{tag}\b)", re.IGNORECASE)
    safe = pattern.sub(lambda match: "\u2039" + match.group(1), text)
    return f"<{tag}>\n{safe}\n</{tag}>"
