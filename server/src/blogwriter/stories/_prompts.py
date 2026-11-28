"""Versioned prompt files (``prompts/<name>.v<N>.md``), loaded with :mod:`importlib.resources`.

A prompt's version is its file name without ``.md`` (``classify.v1``) and is recorded as
``promptVersion`` on what it produced. Change a prompt only by adding a new version file (a
golden test pins the hash of every shipped version). Untrusted text never goes into a system
prompt: it travels in the user message, inside a delimited section (:func:`fenced`).
"""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass, field
from importlib import resources
from typing import Final

__all__ = [
    "ANONYMIZE",
    "CLASSIFY",
    "PROMPT_NAMES",
    "SEED_EXTRACT",
    "Prompt",
    "fenced",
    "load_prompt",
]

ANONYMIZE: Final = "anonymize.v1"
CLASSIFY: Final = "classify.v1"
SEED_EXTRACT: Final = "seed-extract.v1"
PROMPT_NAMES: Final = (ANONYMIZE, CLASSIFY, SEED_EXTRACT)

_PACKAGE: Final = "blogwriter.stories"
_NAME_RE: Final = re.compile(r"[a-z][a-z0-9-]*\.v[0-9]+")
_TAG_RE: Final = re.compile(r"[a-z][a-z_]*")


@dataclass(frozen=True, slots=True)
class Prompt:
    """One prompt file: its version (the file stem) and its text."""

    version: str
    text: str = field(repr=False)


@functools.cache
def load_prompt(name: str) -> Prompt:
    """The prompt ``prompts/<name>.md`` (``name`` like ``classify.v1``)."""
    if not _NAME_RE.fullmatch(name):
        raise ValueError("prompt names look like classify.v1")
    text = resources.files(_PACKAGE).joinpath(f"prompts/{name}.md").read_text(encoding="utf-8")
    body = text.strip()
    if not body:
        raise ValueError(f"prompt {name} is empty")
    return Prompt(version=name, text=body)


def fenced(tag: str, text: str, **attributes: str) -> str:
    """``text`` inside ``<tag ...>`` and ``</tag>``, with any copy of the tag inside the text
    defused (``<`` becomes U+2039) so untrusted text cannot close the section early."""
    if not _TAG_RE.fullmatch(tag):
        raise ValueError("tags are lowercase words")
    for value in attributes.values():
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", value):
            raise ValueError("attribute values are short identifiers")
    pattern = re.compile(rf"<(/?\s*{tag}\b)", re.IGNORECASE)
    safe = pattern.sub(lambda match: "\u2039" + match.group(1), text)
    attrs = "".join(f' {name}="{value}"' for name, value in attributes.items())
    return f"<{tag}{attrs}>\n{safe}\n</{tag}>"
