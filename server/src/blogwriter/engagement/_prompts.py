"""Versioned prompt files (``prompts/<name>.v<N>.md``), loaded with :mod:`importlib.resources`.

A prompt's version is its file name without ``.md`` (``angles.v1``) and is recorded as
``promptVersion`` on what it produced. Change a prompt only by adding a new version file (a
golden test pins the hash of every shipped version).

Untrusted text (other members' posts and comments) never goes into a system prompt: it travels
in the user message inside a delimited section (:func:`section`). :func:`defuse` neutralizes
every section tag inside a text first, so pasted text can neither close its own section early
nor fake an entry of the owner's material.
"""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass, field
from importlib import resources
from typing import Final

__all__ = [
    "ANGLES",
    "EXTRACT",
    "PROMPT_NAMES",
    "SECTION_TAGS",
    "Prompt",
    "defuse",
    "load_prompt",
    "section",
]

ANGLES: Final = "angles.v1"
EXTRACT: Final = "extract.v1"
PROMPT_NAMES: Final = (ANGLES, EXTRACT)

SECTION_TAGS: Final = ("owner_material", "entry", "voice", "my_post", "pasted_text")
"""The tags the user message is built from (every one is defused inside texts)."""

_PACKAGE: Final = "blogwriter.engagement"
_NAME_RE: Final = re.compile(r"[a-z][a-z0-9-]*\.v[0-9]+")
_ATTRIBUTE_RE: Final = re.compile(r"[A-Za-z0-9_-]{1,40}")
_TAG_RE: Final = re.compile(rf"<(\s*/?\s*(?:{'|'.join(SECTION_TAGS)})\b)", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class Prompt:
    """One prompt file: its version (the file stem) and its text."""

    version: str
    text: str = field(repr=False)


@functools.cache
def load_prompt(name: str) -> Prompt:
    """The prompt ``prompts/<name>.md`` (``name`` like ``angles.v1``)."""
    if not _NAME_RE.fullmatch(name):
        raise ValueError("prompt names look like angles.v1")
    raw = resources.files(_PACKAGE).joinpath(f"prompts/{name}.md").read_text(encoding="utf-8")
    body = raw.strip()
    if not body:
        raise ValueError(f"prompt {name} is empty")
    return Prompt(version=name, text=body)


def defuse(text: str) -> str:
    """``text`` with every section tag (opening or closing) defused: ``<`` becomes U+2039."""
    return _TAG_RE.sub(lambda match: "\u2039" + match.group(1), text)


def section(tag: str, body: str, **attributes: str) -> str:
    """``body`` between ``<tag ...>`` and ``</tag>``. ``body`` must already be defused (or be
    made of sections built here); attribute values are short identifiers."""
    if tag not in SECTION_TAGS:
        raise ValueError(f"unknown section tag {tag!r}")
    for value in attributes.values():
        if not _ATTRIBUTE_RE.fullmatch(value):
            raise ValueError("section attribute values are short identifiers")
    attrs = "".join(f' {name}="{value}"' for name, value in attributes.items())
    return f"<{tag}{attrs}>\n{body}\n</{tag}>"
