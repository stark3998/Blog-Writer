"""Short-lived documents (container ``ephemeral``, default TTL 1 day plus ``expires_at``).

:class:`OAuthState` (owner: linkedin transport) holds one OAuth ``state`` for a top-level
redirect to LinkedIn. The ``state`` value itself is a secret: the document id is its SHA-256,
so a database reader cannot replay it. The callback consumes the state exactly once (delete with
If-Match) and checks ``expires_at`` in code.

:class:`SeedText` (owner: stories) holds the public resume or portfolio text a ``stories.seed``
job extracts from, so the job input carries only its id (job inputs are references, never
content). The job deletes it when it finishes; the TTL collects what a crash leaves behind.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import ClassVar, Final, Literal

from pydantic import Field

from blogwriter.domain.base import ExpiringDocument, derived_id, sha256_hex, to_iso

__all__ = ["OAUTH_STATE_TTL", "SEED_TEXT_TTL", "OAuthState", "SeedText"]

OAUTH_STATE_TTL: Final = timedelta(minutes=10)
SEED_TEXT_TTL: Final = timedelta(days=1)
MAX_SEED_CHARS: Final = 60_000


class OAuthState(ExpiringDocument):
    CONTAINER: ClassVar[str] = "ephemeral"
    KIND: ClassVar[str] = "oauth_state"
    OWNER: ClassVar[str] = "linkedin"

    purpose: Literal["linkedin_connect"] = "linkedin_connect"
    return_path: Literal["/settings/integrations"] = "/settings/integrations"
    """A constant: the callback reflects nothing from the request back."""
    nonce: str | None = Field(default=None, max_length=128)

    @staticmethod
    def id_for(state: str) -> str:
        """The document id of a ``state`` value (``oauth-<sha256>``)."""
        return f"oauth-{sha256_hex(state)}"

    @classmethod
    def issue(cls, state: str, *, now: datetime) -> OAuthState:
        """A new state document valid for :data:`OAUTH_STATE_TTL`."""
        stamp = to_iso(now)
        return cls(
            id=cls.id_for(state),
            created_at=stamp,
            updated_at=stamp,
            expires_at=to_iso(now + OAUTH_STATE_TTL),
            ttl=int(OAUTH_STATE_TTL.total_seconds()) * 6,
        )


class SeedText(ExpiringDocument):
    """Public resume or portfolio text waiting for a ``stories.seed`` job (id from its hash)."""

    CONTAINER: ClassVar[str] = "ephemeral"
    KIND: ClassVar[str] = "seed_text"
    OWNER: ClassVar[str] = "stories"

    source: Literal["cv", "portfolio"]
    text: str = Field(min_length=1, max_length=MAX_SEED_CHARS)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @staticmethod
    def id_for(text: str) -> str:
        """``seed-<28 hex>`` of the text: seeding the same text twice is one document."""
        return derived_id("seed", sha256_hex(text))

    @classmethod
    def issue(cls, text: str, source: Literal["cv", "portfolio"], *, now: datetime) -> SeedText:
        """A new seed document valid for :data:`SEED_TEXT_TTL`."""
        stamp = to_iso(now)
        return cls(
            id=cls.id_for(text),
            created_at=stamp,
            updated_at=stamp,
            expires_at=to_iso(now + SEED_TEXT_TTL),
            ttl=int(SEED_TEXT_TTL.total_seconds()),
            source=source,
            text=text,
            sha256=sha256_hex(text),
        )
