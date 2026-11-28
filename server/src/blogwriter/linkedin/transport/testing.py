"""Test helpers for code that uses the LinkedIn transport (no network, no real LinkedIn).

:class:`FakeLinkedIn` is an ``httpx.MockTransport`` handler with scripted answers per
``(method, url)`` that records every request, so a test can count POSTs (the publisher never
retries). Example::

    fake = FakeLinkedIn()
    fake.created()                                   # 201 + x-restli-id
    with client_installed(fake_client(fake)) as client:
        ...                                          # routes and steps use current_client()
    assert len(fake.calls("POST", POSTS_URL)) == 1

:func:`make_linkedin_settings` ignores the environment. :func:`connect_member` stores a
vault-encrypted connection the way the OAuth callback does. :func:`approved_post` builds a post
whose payload is the serializer's output and whose approval is current.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Final, override
from urllib.parse import quote

import httpx
from pydantic import JsonValue
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource

from blogwriter.core.crypto import TokenVault
from blogwriter.db import Store
from blogwriter.domain.base import to_iso
from blogwriter.domain.common import MediaRef
from blogwriter.domain.docstore import upsert_doc
from blogwriter.domain.ports import BlobStore
from blogwriter.domain.posts import Approval, Post
from blogwriter.domain.settings import LINKEDIN_SCOPES, Blocklist, BlocklistTerm
from blogwriter.domain.states import PostStatus
from blogwriter.domain.testing import sample_document
from blogwriter.linkedin.littletext import api_commentary, parse_display
from blogwriter.linkedin.settings import LinkedInSettings
from blogwriter.linkedin.transport.client import (
    API_BASE,
    INTROSPECT_URL,
    TOKEN_URL,
    USERINFO_URL,
    LinkedInClient,
    TokenGrant,
)
from blogwriter.linkedin.transport.tokens import store_connection
from blogwriter.linkedin.transport.updates import revalidated

__all__ = [
    "CLIENT_SECRET",
    "DOC_URN",
    "IMAGE_URNS",
    "MEMBER_SUB",
    "MEMBER_TOKEN",
    "MEMBER_URN",
    "POSTS_URL",
    "POST_URN",
    "UPLOAD_URL",
    "Answer",
    "FakeLinkedIn",
    "approved_post",
    "connect_member",
    "fake_client",
    "make_linkedin_settings",
    "media_status_url",
    "post_media",
    "save_blocklist",
]

POSTS_URL: Final = f"{API_BASE}/posts"
UPLOAD_URL: Final = "https://www.linkedin.com/dms-uploads/sp/D5F10AQE/uploadedDocument/0?ut=cap"
MEMBER_TOKEN: Final = "AQV-member-token-canary-5f1e"  # noqa: S105 - a canary, not a secret
CLIENT_SECRET: Final = "client-secret-canary-91be"  # noqa: S105 - a canary, not a secret
MEMBER_SUB: Final = "abcDEF123"
MEMBER_URN: Final = f"urn:li:person:{MEMBER_SUB}"
POST_URN: Final = "urn:li:share:7300000000000000001"
DOC_URN: Final = "urn:li:document:D4E10AQHdoc1"
IMAGE_URNS: Final = ("urn:li:image:D4E10AQHimg1", "urn:li:image:D4E10AQHimg2")
_T0: Final = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)

type Answer = httpx.Response | Exception | Callable[[httpx.Request], httpx.Response]


class _ArgsOnlyLinkedInSettings(LinkedInSettings):
    """``LinkedInSettings`` from constructor arguments only (never the environment)."""

    @classmethod
    @override
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (init_settings,)


def make_linkedin_settings(**overrides: object) -> LinkedInSettings:
    """Configured test settings (client id/secret, redirect URI); arguments override them."""
    values: dict[str, object] = {
        "client_id": "testclient123",
        "client_secret": CLIENT_SECRET,
        "redirect_uri": "https://writer.test/api/linkedin/oauth/callback",
    }
    values.update(overrides)
    return _ArgsOnlyLinkedInSettings.model_validate(values)


def media_status_url(kind: str, urn: str) -> str:
    return f"{API_BASE}/{kind}s/{quote(urn, safe='')}"


@dataclass
class FakeLinkedIn:
    """A scripted LinkedIn: answers per ``(method, url)`` in order, then the default; every
    request is recorded. An unscripted request raises ``AssertionError``."""

    requests: list[httpx.Request] = field(default_factory=list[httpx.Request])
    queued: dict[tuple[str, str], list[Answer]] = field(
        default_factory=dict[tuple[str, str], list[Answer]]
    )
    defaults: dict[tuple[str, str], Answer] = field(default_factory=dict[tuple[str, str], Answer])

    def script(self, method: str, url: str, *answers: Answer) -> None:
        self.queued.setdefault((method, url), []).extend(answers)

    def always(self, method: str, url: str, answer: Answer) -> None:
        self.defaults[(method, url)] = answer

    def __call__(self, request: httpx.Request) -> httpx.Response:
        request.read()
        self.requests.append(request)
        key = (request.method, str(request.url))
        queue = self.queued.get(key)
        answer: Answer | None = queue.pop(0) if queue else self.defaults.get(key)
        if answer is None:
            raise AssertionError(f"unexpected LinkedIn call: {request.method} {request.url}")
        if isinstance(answer, Exception):
            raise answer
        if isinstance(answer, httpx.Response):
            return answer
        return answer(request)

    def calls(self, method: str, url: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.method == method and str(r.url) == url]

    @staticmethod
    def body(request: httpx.Request) -> JsonValue:
        value: JsonValue = json.loads(request.content)
        return value

    # Typical answers -------------------------------------------------------------------------

    def created(self, urn: str = POST_URN) -> None:
        self.script("POST", POSTS_URL, httpx.Response(201, headers={"x-restli-id": urn}))

    def token_exchange(self, *, scope: str = "openid,profile,w_member_social") -> None:
        self.script(
            "POST",
            TOKEN_URL,
            httpx.Response(
                200,
                json={"access_token": MEMBER_TOKEN, "expires_in": 5_184_000, "scope": scope},
            ),
        )

    def userinfo(self, sub: str = MEMBER_SUB) -> None:
        self.script("GET", USERINFO_URL, httpx.Response(200, json={"sub": sub, "name": "X"}))

    def introspection(self, *, active: bool, expires_at: datetime | None = None) -> None:
        body: dict[str, object] = {"active": active, "status": "active" if active else "revoked"}
        if expires_at is not None:
            body["expires_at"] = int(expires_at.timestamp())
        self.script("POST", INTROSPECT_URL, httpx.Response(200, json=body))

    def upload_flow(self, kind: str, urn: str, *statuses: str) -> None:
        """``initializeUpload`` -> ``PUT`` -> status polls (default: one ``AVAILABLE``)."""
        self.script(
            "POST",
            f"{API_BASE}/{kind}s?action=initializeUpload",
            httpx.Response(200, json={"value": {"uploadUrl": UPLOAD_URL, kind: urn}}),
        )
        self.script("PUT", UPLOAD_URL, httpx.Response(201))
        for status in statuses or ("AVAILABLE",):
            self.script(
                "GET", media_status_url(kind, urn), httpx.Response(200, json={"status": status})
            )


def fake_client(fake: FakeLinkedIn, settings: LinkedInSettings | None = None) -> LinkedInClient:
    """A client whose every request goes to ``fake`` (close it with ``aclose``)."""
    return LinkedInClient(
        settings if settings is not None else make_linkedin_settings(),
        transport=httpx.MockTransport(fake),
    )


async def connect_member(
    store: Store,
    vault: TokenVault,
    *,
    at: datetime = _T0,
    token: str = MEMBER_TOKEN,
    member_urn: str = MEMBER_URN,
    lifetime: timedelta = timedelta(days=60),
) -> None:
    """Store a connected member the way the OAuth callback does."""
    grant = TokenGrant(
        access_token=token, expires_in=int(lifetime.total_seconds()), scopes=LINKEDIN_SCOPES
    )
    await store_connection(store, vault, grant=grant, member_urn=member_urn, now=at)


async def save_blocklist(store: Store, terms: Sequence[str], *, now: datetime = _T0) -> None:
    """Save the blocklist (``terms`` as whole-word client names, ids ``t-1``...)."""
    stamp = to_iso(now)
    blocklist = Blocklist(
        id=Blocklist.SINGLETON_ID,
        created_at=stamp,
        updated_at=stamp,
        configured=True,
        terms=[
            BlocklistTerm(id=f"t-{index + 1}", term=term, category="client")
            for index, term in enumerate(terms)
        ],
    )
    await upsert_doc(store, blocklist)


async def post_media(
    blobs: BlobStore,
    data: bytes,
    *,
    role: str,
    content_type: str,
    name: str,
    alt_text: str | None = None,
    **fields: object,
) -> MediaRef:
    """Put ``data`` in the ``media`` container and return its reference."""
    await blobs.put("media", name, data, content_type=content_type, overwrite=True)
    return MediaRef.model_validate(
        {
            "role": role,
            "container": "media",
            "name": name,
            "sha256": hashlib.sha256(data).hexdigest(),
            "content_type": content_type,
            "size_bytes": len(data),
            "alt_text": alt_text,
            **fields,
        }
    )


def approved_post(
    *,
    display: str,
    hashtags: Sequence[str] = (),
    status: PostStatus = PostStatus.APPROVED,
    approve: bool = True,
    approved_at: datetime = _T0,
    **fields: object,
) -> Post:
    """A post whose payload is ``api_commentary(parse_display(display, hashtags))`` with a
    current approval (``approve=False``: none), in ``status``."""
    payload = api_commentary(parse_display(display, hashtags))
    post = sample_document(
        Post, display_text=display, payload=payload, hashtags=list(hashtags), **fields
    )
    approval = (
        Approval(sha256=post.compute_approval_hash(), approved_at=to_iso(approved_at))
        if approve
        else None
    )
    return revalidated(post, approval=approval, status=status)
