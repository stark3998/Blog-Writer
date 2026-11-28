"""Versioned prompt files (``prompts/<name>.v<N>.md``) and data fences.

A prompt's version is its file name without ``.md`` (``draft.v1``) and is recorded as
``promptVersion`` on the ``servedBy`` record of what it produced. Change a prompt only by adding
a new version file and pointing the constant at it (``tests/pipeline/goldens/prompts.json`` pins
the hash of every shipped version).

Untrusted or owner-supplied text never goes into a system prompt: it travels in the user turn,
inside a delimited section (:func:`fenced`) that the system prompt declares to be data. A copy of
the closing tag inside the data is defused, so a passage cannot end its section early.
"""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass, field
from importlib import resources
from typing import Final

__all__ = [
    "CRITIQUE",
    "DRAFT",
    "INTERVIEW",
    "OUTLINE",
    "PROMPT_NAMES",
    "REVISE",
    "SELECTION",
    "Prompt",
    "fenced",
    "load_prompt",
]

INTERVIEW: Final = "interview.v1"
OUTLINE: Final = "outline.v1"
DRAFT: Final = "draft.v1"
CRITIQUE: Final = "critique.v1"
REVISE: Final = "revise.v1"
SELECTION: Final = "selection.v1"
PROMPT_NAMES: Final = (INTERVIEW, OUTLINE, DRAFT, CRITIQUE, REVISE, SELECTION)

_PACKAGE: Final = "blogwriter.pipeline"
_NAME_RE: Final = re.compile(r"[a-z][a-z0-9-]*\.v[0-9]+")
_TAG_RE: Final = re.compile(r"[a-z][a-z_]*")
_ATTRIBUTE_RE: Final = re.compile(r"[A-Za-z0-9_.-]{1,64}")


@dataclass(frozen=True, slots=True)
class Prompt:
    """One prompt file: its version (the file stem) and its text."""

    version: str
    text: str = field(repr=False)


@functools.cache
def load_prompt(name: str) -> Prompt:
    """The prompt ``prompts/<name>.md`` (``name`` like ``draft.v1``)."""
    if not _NAME_RE.fullmatch(name):
        raise ValueError("prompt names look like draft.v1")
    text = resources.files(_PACKAGE).joinpath(f"prompts/{name}.md").read_text(encoding="utf-8")
    body = text.strip()
    if not body:
        raise ValueError(f"prompt {name} is empty")
    return Prompt(version=name, text=body)


def fenced(tag: str, text: str, **attributes: str) -> str:
    """``text`` inside ``<tag ...>`` and ``</tag>``; any copy of the tag inside ``text`` is
    defused (``<`` becomes U+2039), so the data cannot close its section early."""
    if not _TAG_RE.fullmatch(tag):
        raise ValueError("tags are lowercase words")
    for value in attributes.values():
        if not _ATTRIBUTE_RE.fullmatch(value):
            raise ValueError("attribute values are short identifiers")
    pattern = re.compile(rf"<(/?\s*{tag}\b)", re.IGNORECASE)
    safe = pattern.sub(lambda match: "\u2039" + match.group(1), text)
    attrs = "".join(f' {name}="{value}"' for name, value in attributes.items())
    return f"<{tag}{attrs}>\n{safe}\n</{tag}>"
