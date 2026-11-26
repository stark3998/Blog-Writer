"""What a push notification may carry: a short title, a same-origin link and a tag.

There is deliberately **no body or description field**, and extra fields are refused, so draft
text, post text or anything longer than a title can never be sent through a push service. The
service worker shows the title and opens ``url`` (a confirmation page; GET never changes state).

Wire format (UTF-8 JSON, encrypted by Web Push)::

    {"title": "Post slot in 15 minutes", "url": "/publish/slot-123", "tag": "slot:slot-123"}
"""

from __future__ import annotations

import json
import re
import unicodedata
from typing import Final

from pydantic import BaseModel, ConfigDict, field_validator

__all__ = ["MAX_TITLE_CHARS", "MAX_URL_CHARS", "NotificationPayload"]

#: Longest title, in characters (code points).
MAX_TITLE_CHARS: Final = 80
#: Longest link path, in characters.
MAX_URL_CHARS: Final = 512

# A path starting with exactly one "/" (never "//" or "/\", which browsers treat as another
# origin), then RFC 3986 path, query and fragment characters only.
_URL_RE: Final = re.compile(r"/(?![/\\])[A-Za-z0-9\-._~!$&'()*+,;=:@/%?#]*")
_TAG_RE: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}")
# Bidirectional overrides and isolates can make a title display differently from its text.
_BIDI_CONTROLS: Final = frozenset("\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069")


class NotificationPayload(BaseModel):
    """A push notification: title (at most 80 characters), same-origin path and tag."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    title: str
    """Shown as the notification title. 1-80 characters, no control characters."""

    url: str
    """Same-origin path opened on tap, for example ``/publish/slot-123``."""

    tag: str
    """Notification tag: a newer notification with the same tag replaces the older one."""

    @field_validator("title")
    @classmethod
    def _check_title(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        if len(value) > MAX_TITLE_CHARS:
            raise ValueError(f"must be at most {MAX_TITLE_CHARS} characters")
        for char in value:
            if unicodedata.category(char) == "Cc" or char in _BIDI_CONTROLS:
                raise ValueError("must not contain control characters")
        return value

    @field_validator("url")
    @classmethod
    def _check_url(cls, value: str) -> str:
        if len(value) > MAX_URL_CHARS or not _URL_RE.fullmatch(value):
            raise ValueError(
                "must be a same-origin path such as /publish/slot-1 (one leading '/', "
                f"no scheme or host, URL characters only, at most {MAX_URL_CHARS} characters)"
            )
        return value

    @field_validator("tag")
    @classmethod
    def _check_tag(cls, value: str) -> str:
        if not _TAG_RE.fullmatch(value):
            raise ValueError("must be 1-64 characters of [A-Za-z0-9._:-], starting alphanumeric")
        return value

    def to_json(self) -> str:
        """The exact bytes (as text) sent to the push service: title, url and tag only."""
        document = {"title": self.title, "url": self.url, "tag": self.tag}
        return json.dumps(document, ensure_ascii=False, separators=(",", ":"))
