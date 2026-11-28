"""The shared sanitizer profile implemented with nh3 (ammonia).

Every rule comes from ``contracts/sanitizer-profile.json`` (see :mod:`.profile`): allowed tags,
attributes per tag, a value rule for every attribute that carries a URL, an id, a class or a
style, forced attributes on the task-list checkbox, ``rel="noopener noreferrer"`` on every link,
and the tags dropped together with their content. The portfolio's ``rehype-sanitize`` schema is
generated from the same file, and both run ``contracts/fixtures/xss``.

nh3 is CPU-bound: call :func:`sanitize_html` through ``asyncio.to_thread`` from async code.
"""

from __future__ import annotations

from functools import cache
from typing import Final

import nh3

from .profile import ProfileRules, default_profile_rules

__all__ = ["URL_SCHEMES", "attribute_filter", "build_cleaner", "sanitize_html"]

URL_SCHEMES: Final = frozenset({"https", "mailto"})
"""Absolute URL schemes nh3 lets through before the profile's own ``href``/``src`` patterns
apply (``#fragment`` and site-relative URLs have no scheme)."""


def build_cleaner(rules: ProfileRules) -> nh3.Cleaner:
    """An nh3 cleaner for ``rules``. nh3 removes an attribute whose filter raises, so the filter
    never needs to."""
    profile = rules.profile

    def attribute_value(tag: str, attribute: str, value: str) -> str | None:
        return rules.filter_value(tag, attribute, value)

    return nh3.Cleaner(
        tags=set(profile.tags),
        clean_content_tags=set(profile.drop_with_content),
        attributes={tag: set(names) for tag, names in profile.attributes.items()},
        attribute_filter=attribute_value,
        strip_comments=True,
        link_rel=profile.link_rel,
        url_schemes=set(URL_SCHEMES),
        set_tag_attribute_values={
            tag: dict(values) for tag, values in profile.forced_attributes.items()
        },
    )


@cache
def _default_cleaner() -> nh3.Cleaner:
    return build_cleaner(default_profile_rules())


def attribute_filter(tag: str, attribute: str, value: str) -> str | None:
    """The shared profile's value rule for ``attribute=value`` on ``tag`` (``None`` drops it)."""
    return default_profile_rules().filter_value(tag, attribute, value)


def sanitize_html(html: str) -> str:
    """Sanitize rendered article HTML with the shared profile.

    Raises :class:`~blogwriter.publish.contract.ContractUnavailableError` when the profile cannot
    be loaded (fail closed).
    """
    return _default_cleaner().clean(html)
