"""Staging at T-60, the delayed jobs around a slot, and the expiry of time-sensitive posts.

``posts.stage`` (``SlotJobInput``, id ``stage_job_id(postId, slot)``, ``run_after`` = slot minus
``CadenceSettings.stageLeadMinutes``) gets a one-click post ready for the owner's tap:

``check``
    A no-op when the job starts at or after the slot itself (``STAGE_MAX_LATENESS`` past the
    slot: downtime; the slot check then offers the next free slot), or when the post was
    re-slotted, edited (approval no longer current), switched to the kit or is no longer
    approved. Before the slot it stages however late it starts (an approval inside the stage
    lead stages at once).
``render``
    Everything the post sends (``domain.posts.outgoing_texts``) passes the **current**
    blocklist (a term added after the approval: ``blocklist_hit``; never saved:
    ``blocklist_not_configured``), so nothing is uploaded or offered for the tap. Carousels and
    image cards: every media blob must exist; a missing one is re-rendered and must come back
    byte-identical (else ``media_changed``: the approval no longer covers it).
``upload``
    ``linkedin.transport.upload_post_media`` (URNs recorded before each PUT, polled until
    ``AVAILABLE``; resumable).
``token``
    ``linkedin.transport.access_token`` (and an introspection when the transport offers one).
``ready``
    ``approved -> staged`` with the staging record, then a push "Post at HH:MM: tap to publish"
    opening the go-live page.

Any failure records ``staging.errorCode``/``lastError`` on the post, emits
``emit_alert("needs_attention", postId)`` and pushes "Post at HH:MM needs you". Nothing here can
reach the publisher: publishing happens only in ``POST /api/posts/{id}/publish``.

The transport is reached through :class:`StagingTransport`. The default
(:class:`LinkedInTransport`) imports ``blogwriter.linkedin.transport`` on first use, so the
posts package never depends on the transport at import time; tests pass a fake.
"""

from __future__ import annotations

import importlib
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from types import ModuleType
from typing import Final, Protocol, cast

from pydantic import JsonValue

from blogwriter.db import NotFound, Store
from blogwriter.domain.base import parse_iso, to_iso
from blogwriter.domain.confidentiality import (
    BlocklistNotConfigured,
    ConfidentialText,
    check_texts,
)
from blogwriter.domain.docstore import read_doc, read_singleton, update_doc
from blogwriter.domain.golive import cancel_slot_jobs
from blogwriter.domain.golive import enqueue_slot_jobs as enqueue_golive_slot_jobs
from blogwriter.domain.jobflow import past_max_lateness
from blogwriter.domain.jobtypes import (
    POSTS_STAGE,
    STAGE_MAX_LATENESS,
    SlotJobInput,
)
from blogwriter.domain.ports import JobQueue, Services
from blogwriter.domain.posts import Post, StagingState, outgoing_texts
from blogwriter.domain.settings import Blocklist, BrandKit, CadenceSettings, EmployerPolicy
from blogwriter.domain.states import POST_MACHINE, PostStatus
from blogwriter.linkedin.posts._logging import logger
from blogwriter.linkedin.posts._post import go_live_path
from blogwriter.linkedin.posts.carousel import (
    MEDIA_FORMATS,
    RenderRefused,
    media_missing,
    render_post_media,
    same_media,
)
from blogwriter.notify import NotificationPayload, emit_alert

__all__ = [
    "EXPIRABLE",
    "LinkedInTransport",
    "StagingFailed",
    "StagingTransport",
    "TokenCheck",
    "cancel_slot_jobs",
    "current_for_stage",
    "enqueue_slot_jobs",
    "expire_post",
    "push_owner",
    "stage_blocker",
    "stage_check",
    "stage_media",
    "stage_ready",
    "stage_screen",
    "stage_token",
    "stage_upload",
    "staging_failed",
]

EXPIRABLE: Final = frozenset(
    {PostStatus.DRAFT, PostStatus.APPROVED, PostStatus.STAGED, PostStatus.KIT, PostStatus.MISSED}
)
"""States a time-sensitive post expires from (``posted`` never expires)."""
_LOST_TOKEN: Final = frozenset({"inactive", "expired", "token_unreadable", "not_connected"})
_TRANSIENT_UPLOAD: Final = frozenset({"timeout", "network_error", "bad_response", "upload_timeout"})
_CODE_RE: Final = re.compile(r"[a-z][a-z0-9_.-]{0,63}")


def _transient_upload(code: str) -> bool:
    """An upload failure worth a retry: a timeout, the network, or a LinkedIn 5xx."""
    return code in _TRANSIENT_UPLOAD or (code.startswith("http_5") and code[5:].isdigit())


class StagingFailed(Exception):
    """Staging could not get the post ready. ``code`` lands in ``staging.errorCode``."""

    def __init__(self, code: str, detail: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True, slots=True)
class TokenCheck:
    ok: bool
    code: str | None = None


class StagingTransport(Protocol):
    """What staging needs from the LinkedIn transport."""

    async def upload_media(self, services: Services, post_id: str) -> None:
        """Upload the post's media until LinkedIn reports them ``AVAILABLE`` (resumable).
        Raises :class:`StagingFailed` (``reconnect_required``...)."""
        ...

    async def check_token(self, services: Services, *, now: datetime) -> TokenCheck: ...


type _Upload = Callable[[Services, str], Awaitable[object]]
type _AccessToken = Callable[[Services], Awaitable[tuple[str, str]]]


class LinkedInTransport:
    """:class:`StagingTransport` over ``blogwriter.linkedin.transport`` (W-A9): the module is
    imported on first use, by the names its contract publishes (``upload_post_media``,
    ``access_token``, ``ReconnectRequired``; ``introspect_connection`` and ``current_client``
    when present)."""

    def __init__(self, module: str = "blogwriter.linkedin.transport") -> None:
        self.module = module

    def _module(self) -> ModuleType:
        try:
            return importlib.import_module(self.module)
        except ImportError:
            raise StagingFailed("linkedin_unavailable", "LinkedIn is not set up.") from None

    @staticmethod
    def _exception(module: ModuleType, name: str) -> type[Exception]:
        found: object = getattr(module, name, None)
        if isinstance(found, type) and issubclass(found, Exception):
            return found
        return _NeverRaised

    def _reconnect(self, module: ModuleType) -> type[Exception]:
        return self._exception(module, "ReconnectRequired")

    async def upload_media(self, services: Services, post_id: str) -> None:
        module = self._module()
        upload = cast(_Upload | None, getattr(module, "upload_post_media", None))
        if upload is None:
            raise StagingFailed("linkedin_unavailable", "LinkedIn uploads are not available.")
        try:
            await upload(services, post_id)
        except self._reconnect(module):
            raise StagingFailed("reconnect_required", "Reconnect LinkedIn.") from None
        except self._exception(module, "UploadFailed") as exc:
            code: object = getattr(exc, "code", None)
            if isinstance(code, str) and _transient_upload(code):
                raise  # LinkedIn hiccup: the runner retries; the last attempt alerts
            name = code if isinstance(code, str) and _CODE_RE.fullmatch(code) else "upload_failed"
            raise StagingFailed(name, "LinkedIn did not take the media.") from None

    async def check_token(self, services: Services, *, now: datetime) -> TokenCheck:
        module = self._module()
        access = cast(_AccessToken | None, getattr(module, "access_token", None))
        if access is None:
            return TokenCheck(False, "linkedin_unavailable")
        try:
            await access(services)
        except self._reconnect(module):
            return TokenCheck(False, "reconnect_required")
        introspect = cast(
            Callable[..., Awaitable[object]] | None, getattr(module, "introspect_connection", None)
        )
        current_client = cast(Callable[[], object] | None, getattr(module, "current_client", None))
        if introspect is not None and current_client is not None:
            result = await introspect(services, current_client(), now=now)
            outcome: object = getattr(result, "outcome", None)
            if isinstance(outcome, str) and outcome in _LOST_TOKEN:
                return TokenCheck(False, "reconnect_required")
        return TokenCheck(True)


class _NeverRaised(Exception):
    """Stands in for ``ReconnectRequired`` when the transport does not define it."""


# --------------------------------------------------------------------------------------------
# Pushes and alerts
# --------------------------------------------------------------------------------------------


async def push_owner(services: Services, *, title: str, url: str, tag: str) -> bool:
    """Best-effort push (title and same-origin link only); never raises."""
    try:
        payload = NotificationPayload(title=title[:80], url=url, tag=tag)
        result = await services.notifier.notify(payload, urgency="high")
    except Exception as exc:  # a push problem (or no notifier) must never undo what happened
        logger.warning("posts_push_failed", extra={"error_code": type(exc).__name__})
        return False
    return result.any_delivered


def _slot_time(post: Post | None, slot_utc: str) -> str:
    if post is not None and post.slot is not None and post.slot.utc == slot_utc:
        return post.slot.local_time
    return f"{parse_iso(slot_utc):%H:%M} UTC"


# --------------------------------------------------------------------------------------------
# Slot jobs
# --------------------------------------------------------------------------------------------


async def enqueue_slot_jobs(jobs: JobQueue, post: Post, *, cadence: CadenceSettings) -> str | None:
    """For a one-click post with a slot: ``posts.stage`` and ``planner.slot-check``
    (``domain.golive.enqueue_slot_jobs``). Returns the stage job id."""
    return (await enqueue_golive_slot_jobs(jobs, post, cadence=cadence)).get(POSTS_STAGE)


# --------------------------------------------------------------------------------------------
# Staging steps
# --------------------------------------------------------------------------------------------


def stage_blocker(post: Post, slot_utc: str) -> str | None:
    """Why staging for ``slot_utc`` no longer applies to ``post`` (``None``: it does)."""
    if post.status not in (PostStatus.APPROVED, PostStatus.STAGED):
        return "status"
    if post.slot is None or post.slot.utc != slot_utc:
        return "slot_changed"
    if not post.approval_current:
        return "approval_changed"
    if post.go_live_mode != "one_click":
        return "kit_mode"
    return None


async def _read(store: Store, post_id: str) -> Post | None:
    try:
        return (await read_doc(store, Post, post_id)).doc
    except NotFound:
        return None


async def stage_check(
    services: Services, job_input: SlotJobInput, *, now: datetime
) -> dict[str, JsonValue]:
    """The first step: ``{"approvalSha256": ...}`` to go on, or ``{"skipped": reason}``."""
    if now >= parse_iso(job_input.slot_utc) or past_max_lateness(
        job_input.slot_utc, now, STAGE_MAX_LATENESS
    ):
        logger.info("posts_stage_late", extra={"post_id": job_input.post_id})
        return {"skipped": "late"}
    post = await _read(services.store, job_input.post_id)
    if post is None:
        return {"skipped": "missing"}
    blocker = stage_blocker(post, job_input.slot_utc)
    if blocker is not None or post.approval is None:
        return {"skipped": blocker or "approval_changed"}
    return {"approvalSha256": post.approval.sha256}


async def current_for_stage(
    services: Services, job_input: SlotJobInput, approval_sha256: str
) -> Post | None:
    """The post if staging still applies with the same approval, else ``None``."""
    post = await _read(services.store, job_input.post_id)
    if post is None or stage_blocker(post, job_input.slot_utc) is not None:
        return None
    if post.approval is None or post.approval.sha256 != approval_sha256:
        return None
    return post


async def stage_screen(services: Services, post: Post, *, now: datetime) -> None:
    """The current blocklist over everything the post sends (it may have gained a term since
    the approval): :class:`StagingFailed` ``blocklist_hit`` / ``blocklist_not_configured``."""
    blocklist, _ = await read_singleton(services.store, Blocklist, now=to_iso(now))
    try:
        check_texts(outgoing_texts(post), blocklist)
    except BlocklistNotConfigured:
        raise StagingFailed(
            "blocklist_not_configured", "Save the blocklist once in Settings > Policy."
        ) from None
    except ConfidentialText:
        raise StagingFailed(
            "blocklist_hit", "The post matches the blocklist. Edit it and approve again."
        ) from None


async def stage_media(services: Services, post: Post, *, now: datetime) -> None:
    """Every media blob exists; a missing one is re-rendered and must be byte-identical."""
    if post.format not in MEDIA_FORMATS:
        return
    if media_missing(post):
        raise StagingFailed("media_missing", "The post has no rendered media.")
    blobs = services.blobs
    if blobs is None:
        raise StagingFailed("storage_unavailable", "Storage is not configured.")
    missing = [item for item in post.media if await blobs.head(item.container, item.name) is None]
    if not missing:
        return
    render = services.render
    if render is None:
        raise StagingFailed("render_unavailable", "Rendering is not configured.")
    stamp = to_iso(now)
    brand, _ = await read_singleton(services.store, BrandKit, now=stamp)
    blocklist, _ = await read_singleton(services.store, Blocklist, now=stamp)
    policy, _ = await read_singleton(services.store, EmployerPolicy, now=stamp)
    try:
        rendered = await render_post_media(
            post,
            render=render,
            blobs=blobs,
            brand=brand,
            blocklist=blocklist,
            disclaimer=policy.disclaimer_short,
        )
    except RenderRefused as exc:
        raise StagingFailed(exc.code, exc.detail) from None
    if not same_media(rendered.media, post.media):
        raise StagingFailed(
            "media_changed", "The re-rendered media differ from the approved ones. Approve again."
        )


async def stage_upload(services: Services, post: Post, transport: StagingTransport) -> bool:
    """Upload the media LinkedIn needs (none for text posts). ``True`` when it uploaded."""
    if post.format not in MEDIA_FORMATS:
        return False
    await transport.upload_media(services, post.id)
    return True


async def stage_token(services: Services, transport: StagingTransport, *, now: datetime) -> None:
    check = await transport.check_token(services, now=now)
    if not check.ok:
        raise StagingFailed(check.code or "reconnect_required", "Reconnect LinkedIn.")


async def stage_ready(
    services: Services,
    job_input: SlotJobInput,
    approval_sha256: str,
    *,
    job_id: str,
    now: datetime,
) -> Post | None:
    """``approved -> staged`` with the staging record and the push. ``None`` when staging no
    longer applies (nothing written, nothing pushed); an already staged post is returned."""
    stamp = to_iso(now)

    def mutate(current: Post) -> Post | None:
        if stage_blocker(current, job_input.slot_utc) is not None:
            return None
        if current.approval is None or current.approval.sha256 != approval_sha256:
            return None
        if current.status is PostStatus.STAGED:
            return None
        target = POST_MACHINE.transition(current.status, PostStatus.STAGED)
        return current.model_copy(
            update={
                "status": target,
                "status_changed_at": stamp,
                "updated_at": stamp,
                "last_error": None,
                "staging": StagingState(
                    slot_utc=job_input.slot_utc,
                    job_id=job_id,
                    staged_at=stamp,
                    media_ready=True,
                    token_ok=True,
                    approval_sha256=approval_sha256,
                ),
            }
        )

    try:
        stored = await update_doc(services.store, Post, job_input.post_id, mutate)
    except NotFound:
        return None
    post = stored.doc
    if post.status is not PostStatus.STAGED or post.slot is None:
        return None
    if post.slot.utc != job_input.slot_utc:
        return None
    await push_owner(
        services,
        title=f"Post at {_slot_time(post, job_input.slot_utc)}: tap to publish",
        url=go_live_path(post),
        tag=f"stage:{post.id}",
    )
    return post


async def staging_failed(
    services: Services,
    job_input: SlotJobInput,
    failure: StagingFailed,
    *,
    job_id: str,
    now: datetime,
) -> None:
    """Record the failure on the post (when the slot is unchanged), alert, push."""
    stamp = to_iso(now)

    def mutate(current: Post) -> Post | None:
        if current.slot is None or current.slot.utc != job_input.slot_utc:
            return None
        return current.model_copy(
            update={
                "updated_at": stamp,
                "last_error": failure.code,
                "staging": StagingState(
                    slot_utc=job_input.slot_utc,
                    job_id=job_id,
                    error_code=failure.code,
                ),
            }
        )

    try:
        post = (await update_doc(services.store, Post, job_input.post_id, mutate)).doc
    except NotFound:
        return
    logger.warning(
        "posts_stage_failed", extra={"post_id": job_input.post_id, "error_code": failure.code}
    )
    if post.slot is not None and post.slot.utc == job_input.slot_utc:
        # The slot still stands: the owner has to act before it.
        emit_alert("needs_attention", job_input.post_id)
        await push_owner(
            services,
            title=f"Post at {_slot_time(post, job_input.slot_utc)} needs you",
            url=go_live_path(post),
            tag=f"stage:{post.id}",
        )


# --------------------------------------------------------------------------------------------
# Expiry
# --------------------------------------------------------------------------------------------


async def expire_post(store: Store, post_id: str, *, now: datetime) -> str:
    """``posts.expire``: a time-sensitive post past ``expiresAt`` goes to ``expired`` unless it
    was posted (or is past the point of expiring). Returns the outcome code."""
    stamp = to_iso(now)
    outcome = "not_found"

    def mutate(current: Post) -> Post | None:
        nonlocal outcome
        if current.expires_at is None or parse_iso(current.expires_at) > now:
            outcome = "not_due"
            return None
        if current.status not in EXPIRABLE:
            outcome = f"kept_{current.status.value}"
            return None
        target = POST_MACHINE.transition(current.status, PostStatus.EXPIRED)
        outcome = "expired"
        return current.model_copy(
            update={
                "status": target,
                "status_changed_at": stamp,
                "updated_at": stamp,
                "staging": None,
            }
        )

    try:
        await update_doc(store, Post, post_id, mutate)
    except NotFound:
        return "not_found"
    return outcome
