"""Uploading a post's media to LinkedIn: carousel documents and card images.

For each item of :func:`~blogwriter.linkedin.transport.content.linkedin_media` that is not
``available`` yet:

1. read the bytes from Blob and check them against the item's SHA-256 (fail closed: the bytes
   the owner approved are the bytes that go out);
2. ``initializeUpload`` -> ``(uploadUrl, urn)``; the URN is recorded on the post with
   ``linkedinStatus = uploading`` (a CAS write) **before** the ``PUT``, so a crash never loses a
   remote id;
3. ``PUT`` the bytes;
4. poll the status until ``AVAILABLE`` (``BW_LINKEDIN_UPLOAD_POLL_SECONDS``); when the token may
   not read the status, wait :data:`UNREADABLE_WAIT_SECONDS` instead (what the Phase 0 spike
   does);
5. record ``available``.

**Resumable.** An item already ``uploading`` with a URN is re-attached: its status is read first
and polled while LinkedIn is ``PROCESSING``. Only an upload LinkedIn never received
(``WAITING_UPLOAD``), one it failed to process, or one whose status cannot be read starts over
with a fresh ``initializeUpload`` (the old URN is simply never used).

Only approved bytes are uploaded: the post must be ``approved`` or ``staged`` with a current
approval, checked again on every write. Media URNs and upload states are not part of the
approval hash, so recording them never invalidates an approval.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Final, Literal

from blogwriter.core.logging import register_log_fields
from blogwriter.db import Store
from blogwriter.domain.base import to_iso
from blogwriter.domain.common import MediaRef
from blogwriter.domain.docstore import Stored, read_doc, update_doc
from blogwriter.domain.ports import BlobNotFound, BlobStore, Services
from blogwriter.domain.posts import Post
from blogwriter.domain.states import PostStatus
from blogwriter.linkedin.transport.client import (
    LinkedInClient,
    LinkedInError,
    MediaKind,
    current_client,
)
from blogwriter.linkedin.transport.content import linkedin_media, media_kind
from blogwriter.linkedin.transport.tokens import (
    ReconnectRequired,
    access_token,
    mark_needs_reconnect,
)
from blogwriter.linkedin.transport.updates import revalidated

__all__ = [
    "POLL_INTERVAL_SECONDS",
    "UNREADABLE_WAIT_SECONDS",
    "UPLOADABLE_STATUSES",
    "UploadFailed",
    "upload_post_media",
]

POLL_INTERVAL_SECONDS: Final = 3.0
UNREADABLE_WAIT_SECONDS: Final = 10.0
UPLOADABLE_STATUSES: Final = frozenset({PostStatus.APPROVED, PostStatus.STAGED})
_RESTART: Final = frozenset({"WAITING_UPLOAD", "PROCESSING_FAILED", "UNREADABLE", "CLIENT_ERROR"})
_FAILED: Final = frozenset({"PROCESSING_FAILED", "CLIENT_ERROR"})

type Sleep = Callable[[float], Awaitable[None]]
type UploadStatus = Literal["uploading", "available", "failed"]

_logger: Final = logging.getLogger("blogwriter.linkedin.upload")
register_log_fields("post_id", "media_kind", "error_code")


class UploadFailed(Exception):
    """The media could not be made ``AVAILABLE`` on LinkedIn. ``code`` (content-free):
    ``not_approved``, ``media_changed``, ``blob_missing``, ``media_integrity``,
    ``storage_unavailable``, ``linkedin_unavailable``, ``processing_failed``,
    ``upload_timeout``, or a :class:`~blogwriter.linkedin.transport.client.LinkedInError` code
    (``http_<status>``, ``timeout``, ``network_error``, ``bad_response``,
    ``upload_url_refused``)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _same_item(a: MediaRef, b: MediaRef) -> bool:
    return a.role == b.role and a.name == b.name and a.sha256 == b.sha256


def _check_uploadable(post: Post) -> None:
    if post.status not in UPLOADABLE_STATUSES or not post.approval_current:
        raise UploadFailed("not_approved")


async def _record(
    store: Store,
    post_id: str,
    item: MediaRef,
    urn: str,
    status: UploadStatus,
    *,
    now: datetime,
) -> Stored[Post]:
    """Set the item's URN and upload status (CAS). ``uploading`` always takes the new URN; the
    later states apply only while the item still carries ``urn`` (a concurrent upload that
    replaced it wins)."""
    stamp = to_iso(now)

    def mutate(post: Post) -> Post | None:
        _check_uploadable(post)
        media = list(post.media)
        for index, current in enumerate(media):
            if not _same_item(current, item):
                continue
            if status != "uploading" and current.linkedin_urn != urn:
                return None
            if current.linkedin_urn == urn and current.linkedin_status == status:
                return None
            media[index] = revalidated(current, linkedin_urn=urn, linkedin_status=status)
            return revalidated(post, media=media, updated_at=stamp)
        raise UploadFailed("media_changed")

    return await update_doc(store, Post, post_id, mutate)


async def _read_bytes(blobs: BlobStore, item: MediaRef) -> bytes:
    try:
        data = await blobs.get(item.container, item.name)
    except BlobNotFound:
        raise UploadFailed("blob_missing") from None
    if hashlib.sha256(data).hexdigest() != item.sha256:
        raise UploadFailed("media_integrity")
    return data


async def _linkedin[T](services: Services, call: Awaitable[T]) -> T:
    """Await a LinkedIn call: 401 -> ``needs_reconnect`` + :class:`ReconnectRequired`; any
    other failure -> :class:`UploadFailed` with the client's code."""
    try:
        return await call
    except LinkedInError as exc:
        if exc.unauthorized:
            await mark_needs_reconnect(
                services.store, now=services.clock.now(), code="unauthorized"
            )
            raise ReconnectRequired("unauthorized") from None
        raise UploadFailed(exc.code) from None


class _Uploader:
    def __init__(
        self,
        services: Services,
        client: LinkedInClient,
        post_id: str,
        token: str,
        member_urn: str,
        sleep: Sleep,
    ) -> None:
        self.services = services
        self.client = client
        self.post_id = post_id
        self.token = token
        self.member_urn = member_urn
        self.sleep = sleep
        interval = POLL_INTERVAL_SECONDS
        self.polls = max(1, math.ceil(client.settings.upload_poll_seconds / interval))

    async def record(self, item: MediaRef, urn: str, status: UploadStatus) -> Stored[Post]:
        return await _record(
            self.services.store, self.post_id, item, urn, status, now=self.services.clock.now()
        )

    async def status(self, kind: MediaKind, urn: str) -> str:
        return await _linkedin(self.services, self.client.media_status(self.token, kind, urn))

    async def until_available(self, item: MediaRef, kind: MediaKind, urn: str) -> Stored[Post]:
        for attempt in range(self.polls):
            status = await self.status(kind, urn)
            if status == "AVAILABLE":
                return await self.record(item, urn, "available")
            if status == "UNREADABLE":
                # The token may not read the upload: fall back to a fixed wait (Phase 0 spike).
                await self.sleep(UNREADABLE_WAIT_SECONDS)
                return await self.record(item, urn, "available")
            if status in _FAILED:
                await self.record(item, urn, "failed")
                raise UploadFailed("processing_failed")
            if attempt + 1 < self.polls:
                await self.sleep(POLL_INTERVAL_SECONDS)
        raise UploadFailed("upload_timeout")

    async def upload(self, item: MediaRef, blobs: BlobStore) -> Stored[Post]:
        kind = media_kind(item)
        if item.linkedin_status == "uploading" and item.linkedin_urn:
            urn = item.linkedin_urn
            status = await self.status(kind, urn)
            if status == "AVAILABLE":
                return await self.record(item, urn, "available")
            if status not in _RESTART:
                _logger.info(
                    "linkedin_upload_reattached",
                    extra={"post_id": self.post_id, "media_kind": kind},
                )
                return await self.until_available(item, kind, urn)
        data = await _read_bytes(blobs, item)
        upload_url, urn = await _linkedin(
            self.services, self.client.initialize_upload(self.token, kind, self.member_urn)
        )
        await self.record(item, urn, "uploading")  # the remote id is durable before the PUT
        await _linkedin(
            self.services,
            self.client.upload_bytes(upload_url, data, item.content_type, token=self.token),
        )
        _logger.info("linkedin_upload_sent", extra={"post_id": self.post_id, "media_kind": kind})
        return await self.until_available(item, kind, urn)


def _pending(post: Post) -> list[MediaRef]:
    return [
        item
        for item in linkedin_media(post)
        if not (item.linkedin_status == "available" and item.linkedin_urn)
    ]


async def upload_post_media(
    services: Services,
    post_id: str,
    *,
    client: LinkedInClient | None = None,
    sleep: Sleep = asyncio.sleep,
) -> Stored[Post]:
    """Make every LinkedIn media item of the post ``available`` (see the module docs).

    Returns the post as last written. Raises :class:`UploadFailed`, or
    :class:`~blogwriter.linkedin.transport.tokens.ReconnectRequired` when the connection cannot
    be used. Never touches LinkedIn for a post without media to upload.
    """
    store = services.store
    stored = await read_doc(store, Post, post_id)
    _check_uploadable(stored.doc)
    pending = _pending(stored.doc)
    if not pending:
        return stored
    linkedin = client if client is not None else current_client()
    if linkedin is None:
        raise UploadFailed("linkedin_unavailable")
    blobs = services.blobs
    if blobs is None:
        raise UploadFailed("storage_unavailable")
    token, member_urn = await access_token(services)
    uploader = _Uploader(services, linkedin, post_id, token, member_urn, sleep)
    for item in pending:
        stored = await uploader.upload(item, blobs)
    return stored
