"""The shared sanitizer profile (``contracts/sanitizer-profile.json``) as value rules for nh3.

The profile is data shared with the portfolio's ``rehype-sanitize`` schema. It is loaded the way
:mod:`blogwriter.publish.contract` loads it (the same directory lookup, duplicate keys refused,
:class:`~blogwriter.publish.contract.SanitizerProfile` checks: known keys, the implemented
``version``, ECMA-262 patterns translated for Python), so the sanitizer and the contract's HTML
audit always apply the same rules. A missing or malformed profile raises
:class:`~blogwriter.publish.contract.ContractUnavailableError`: rendering fails closed.

:class:`ProfileRules` turns the profile into the per-attribute value checks the sanitizer and the
renderer use (``href``/``src`` rules, class filtering, ...).
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Final

from .contract import (
    PROFILE_FILE,
    ContractUnavailableError,
    SanitizerProfile,
    find_contracts_dir,
)

__all__ = [
    "BOOLEAN_ATTRIBUTES",
    "FREE_TEXT_ATTRIBUTES",
    "ProfileRules",
    "default_profile_rules",
    "load_profile",
]

FREE_TEXT_ATTRIBUTES: Final = frozenset({"alt", "title"})
"""Attributes whose value is any text (nh3 escapes it)."""
BOOLEAN_ATTRIBUTES: Final = frozenset({"checked", "disabled"})
_ASCII_WHITESPACE_RE: Final = re.compile(r"[\t\n\f\r ]+")


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate key {key!r}")
        result[key] = value
    return result


def load_profile(directory: Path | None = None) -> SanitizerProfile:
    """Read and check ``sanitizer-profile.json`` from ``directory`` (default: the contracts
    directory :func:`~blogwriter.publish.contract.find_contracts_dir` finds)."""
    root = find_contracts_dir() if directory is None else directory
    path = root / PROFILE_FILE
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise ContractUnavailableError(f"cannot read {path}: {error.strerror}") from error
    try:
        data: object = json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except ValueError as error:
        raise ContractUnavailableError(f"{PROFILE_FILE} is not valid JSON: {error}") from error
    return SanitizerProfile.from_json(data)


@dataclass(frozen=True, slots=True)
class ProfileRules:
    """Value rules derived from a :class:`SanitizerProfile`."""

    profile: SanitizerProfile

    @property
    def tags(self) -> frozenset[str]:
        return self.profile.tags

    @property
    def attributes(self) -> Mapping[str, frozenset[str]]:
        return self.profile.attributes

    def allowed_href(self, value: str) -> bool:
        """``href`` rule: https, mailto, ``#fragment`` or site-relative (one leading ``/``)."""
        return self._url_allowed("href", value)

    def allowed_src(self, value: str) -> bool:
        """``src`` rule: https or site-relative."""
        return self._url_allowed("src", value)

    def _url_allowed(self, name: str, value: str) -> bool:
        return any(pattern.search(value) for pattern in self.profile.url_patterns.get(name, ()))

    def keep_classes(self, tag: str, value: str) -> str | None:
        """The class names of ``value`` the profile allows on ``tag`` (``None`` if none)."""
        rule = self.profile.class_patterns.get(tag)
        if rule is None:
            return None
        kept = [name for name in _ASCII_WHITESPACE_RE.split(value) if name and rule.search(name)]
        return " ".join(kept) if kept else None

    def filter_value(self, tag: str, attribute: str, value: str) -> str | None:
        """The value to keep for ``attribute=value`` on an allowed ``tag``; ``None`` drops it.

        ``rel`` on ``<a>`` is only ever the one nh3 adds itself (``link_rel``), never taken from
        the input, because ``rel`` is not in the allowed attributes.
        """
        if tag == "a" and attribute == "rel":
            return value if value == self.profile.link_rel else None
        if attribute not in self.profile.attributes.get(tag, frozenset()):
            return None
        if attribute in BOOLEAN_ATTRIBUTES:
            return attribute
        if attribute in FREE_TEXT_ATTRIBUTES:
            return value
        if attribute == "class":
            return self.keep_classes(tag, value)
        if attribute == "id":
            rule = self.profile.id_patterns.get(tag)
            return value if rule is not None and rule.search(value) else None
        if attribute == "style":
            compact = value.replace(" ", "").rstrip(";").lower()
            return compact if compact in self.profile.style_values else None
        if attribute in self.profile.url_patterns:
            return value if self._url_allowed(attribute, value) else None
        pattern = self.profile.value_patterns.get(attribute)
        return value if pattern is not None and pattern.search(value) else None


@cache
def default_profile_rules() -> ProfileRules:
    """Rules from the contracts directory's profile, loaded once per process."""
    return ProfileRules(load_profile())
