"""Time: an injectable UTC clock and the fixed-width ISO-8601 format stored in documents.

Every timestamp the jobs package writes looks like ``2026-11-26T15:04:05.123456Z``: UTC,
microseconds always present. Fixed width makes ordinal string comparison equal to time
comparison, which the store's range filters (``leaseUntil < now``) rely on.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Final, Protocol

__all__ = ["SYSTEM_CLOCK", "Clock", "SystemClock", "is_timestamp", "parse_iso", "to_iso"]

_ISO_RE: Final = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z")


class Clock(Protocol):
    """A source of the current time. Inject a fake one in tests."""

    def now(self) -> datetime:
        """The current time as a timezone-aware UTC datetime."""
        ...


class SystemClock:
    """The real clock."""

    def now(self) -> datetime:
        return datetime.now(UTC)


SYSTEM_CLOCK: Final = SystemClock()


def to_iso(moment: datetime) -> str:
    """Format an aware datetime as fixed-width ISO-8601 UTC (``...T..:..:...ffffffZ``)."""
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware")
    return moment.astimezone(UTC).replace(tzinfo=None).isoformat(timespec="microseconds") + "Z"


def parse_iso(text: str) -> datetime:
    """Parse a timestamp written by :func:`to_iso`; anything else raises ``ValueError``."""
    if not _ISO_RE.fullmatch(text):
        raise ValueError("timestamp must look like 2026-11-26T15:04:05.000000Z")
    return datetime.fromisoformat(text[:-1]).replace(tzinfo=UTC)


def is_timestamp(value: object) -> bool:
    """Whether ``value`` is a string :func:`parse_iso` accepts."""
    if not isinstance(value, str):
        return False
    try:
        parse_iso(value)
    except ValueError:
        return False
    return True
