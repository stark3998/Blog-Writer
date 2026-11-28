"""The LinkedIn publisher: one owner tap, one intent, one ``POST /rest/posts``, never retried.

Used by exactly one route, ``POST /api/posts/{id}/publish``. No job, step, schedule item or
other route imports this module (an import-graph test over ``blogwriter.*`` checks it), and
:func:`publish_post` only runs with an :class:`OwnerClick`, which only :func:`owner_click` can
build: a FastAPI dependency that needs the authenticated owner's request (auth and CSRF ran in
core's middleware) carrying a confirm token from the go-live page.

**Confirm tokens** are stateless HMACs, ``v1.<issued unix seconds>.<base64url sig>`` with
``sig = HMAC-SHA256(key, "<postId>|<approvalSha256>|<issued>")`` and ``key =
HMAC-SHA256(BW_COOKIE_KEY, b"blogwriter/linkedin-confirm-token/v1")`` (:func:`confirm_key`), so a
token survives a restart and a revision overlap. Outside prod without ``BW_COOKIE_KEY`` the key
is random per process, like core's cookie key. They live ``BW_LINKEDIN_CONFIRM_TOKEN_TTL_SECONDS``.

**The tap** (:func:`publish_post`):

1. The post is ``approved`` or ``staged``, its approval is current and equals the click's, the
   connection is usable; else 409 ``not_approved``, ``approval_stale``, ``reconnect_required``.
   Everything the post sends (``domain.posts.outgoing_texts``) passes the **current**
   blocklist, which may have gained a term since the post was approved (422
   ``confidential_text``; 409 ``blocklist_not_configured``). Media not yet ``AVAILABLE`` on
   LinkedIn is uploaded first (resumable).
2. One CAS write (the client's If-Match) moves it to ``publishing`` with a ``pending`` intent.
   A 412 means another tap won: stop.
3. One ``create_post`` within :data:`CREATE_BUDGET_SECONDS`, then one settling write through
   :func:`~blogwriter.domain.posts.settle_publish_intent`: 201 + URN -> ``posted`` (URN, time,
   URL, final text = display text; ``planner.post-live`` enqueued); a definite 4xx -> back to
   ``approved`` with the intent ``failed`` (a 401 also marks the connection
   ``needs_reconnect``); a 5xx, timeout or network error -> ``needs_attention`` with the intent
   ``unknown``, an out-of-band alert and a push asking the owner to check LinkedIn. The attempt
   runs shielded from request cancellation, so it always settles; a crash leaves the intent for
   the startup sweep (``publishing`` older than 2 minutes -> ``needs_attention``).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import logging
import re
import secrets
import weakref
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Final

from pydantic import Field

from blogwriter.core.auth import OwnerPrincipal
from blogwriter.core.errors import ProblemError
from blogwriter.core.logging import register_log_fields
from blogwriter.core.settings import CoreSettings
from blogwriter.db import PreconditionFailed, StoreUnavailable
from blogwriter.domain.api import ApiModel
from blogwriter.domain.base import Sha256Hex, parse_iso, to_iso
from blogwriter.domain.confidentiality import check_texts
from blogwriter.domain.deps import ClockDep, etag_mismatch
from blogwriter.domain.docstore import Stored, read_doc, read_singleton, replace_doc, update_doc
from blogwriter.domain.ports import Services
from blogwriter.domain.posts import (
    Post,
    PublishIntent,
    PublishOutcome,
    outgoing_texts,
    settle_publish_intent,
)
from blogwriter.domain.settings import Blocklist
from blogwriter.domain.states import POST_MACHINE, PostStatus, TransitionRefused
from blogwriter.linkedin.transport.client import (
    CreateOutcome,
    LinkedInClient,
    current_client,
)
from blogwriter.linkedin.transport.content import (
    PostBodyError,
    linkedin_media,
    media_ready,
    post_body,
    post_url_for,
)
from blogwriter.linkedin.transport.deps import CoreSettingsDep, LinkedInSettingsDep
from blogwriter.linkedin.transport.hooks import enqueue_post_live
from blogwriter.linkedin.transport.tokens import (
    ReconnectRequired,
    access_token,
    mark_needs_reconnect,
    notify_owner,
)
from blogwriter.linkedin.transport.updates import revalidated
from blogwriter.linkedin.transport.upload import UploadFailed, upload_post_media
from blogwriter.notify import emit_alert

__all__ = [
    "CONFIRM_KEY_LABEL",
    "CREATE_BUDGET_SECONDS",
    "PUBLISHABLE_STATUSES",
    "OwnerClick",
    "PublishIn",
    "confirm_key",
    "go_live_path",
    "issue_confirm_token",
    "owner_click",
    "publish_post",
    "reconnect_problem",
    "verify_confirm_token",
]

CONFIRM_KEY_LABEL: Final = b"blogwriter/linkedin-confirm-token/v1"
CREATE_BUDGET_SECONDS: Final = 30.0
PUBLISHABLE_STATUSES: Final = frozenset({PostStatus.APPROVED, PostStatus.STAGED})
SETTLE_ATTEMPTS: Final = 4
SETTLE_BACKOFF_SECONDS: Final = 0.5
CLOCK_SKEW_SECONDS: Final = 60
_TOKEN_RE: Final = re.compile(r"^v1\.([1-9][0-9]{0,11})\.([A-Za-z0-9_-]{43})$")
_PROCESS_KEY: Final = secrets.token_bytes(32)
"""Outside prod without ``BW_COOKIE_KEY``: tokens verify only in this process (like cookies)."""

_logger: Final = logging.getLogger("blogwriter.linkedin.publisher")
register_log_fields("post_id", "intent_id", "outcome", "error_code", "linkedin_urn")

# --------------------------------------------------------------------------------------------
# Confirm tokens
# --------------------------------------------------------------------------------------------


def confirm_key(settings: CoreSettings) -> bytes:
    """The confirm-token key: ``HMAC-SHA256(BW_COOKIE_KEY, CONFIRM_KEY_LABEL)``."""
    if settings.cookie_key is None:
        return _PROCESS_KEY
    secret = settings.cookie_key.get_secret_value().encode("utf-8")
    return hmac.new(secret, CONFIRM_KEY_LABEL, hashlib.sha256).digest()


def _signature(key: bytes, post_id: str, approval_sha256: str, issued: int) -> str:
    message = f"{post_id}|{approval_sha256}|{issued}".encode()
    digest = hmac.new(key, message, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def issue_confirm_token(
    post_id: str, approval_sha256: str, *, now: datetime, key: bytes, ttl_seconds: int
) -> tuple[str, str]:
    """``(token, expiresAt)`` for a go-live page showing ``post_id`` at ``approval_sha256``."""
    issued = int(now.timestamp())
    token = f"v1.{issued}.{_signature(key, post_id, approval_sha256, issued)}"
    return token, to_iso(now.replace(microsecond=0) + timedelta(seconds=ttl_seconds))


def verify_confirm_token(
    token: str,
    post_id: str,
    approval_sha256: str,
    *,
    now: datetime,
    key: bytes,
    ttl_seconds: int,
) -> bool:
    """Whether ``token`` was issued for this post and approval, within ``ttl_seconds``."""
    match = _TOKEN_RE.fullmatch(token)
    if match is None:
        return False
    issued = int(match.group(1))
    expected = _signature(key, post_id, approval_sha256, issued)
    if not hmac.compare_digest(expected, match.group(2)):
        return False
    age = now.timestamp() - issued
    return -CLOCK_SKEW_SECONDS <= age <= ttl_seconds


# --------------------------------------------------------------------------------------------
# The owner's click
# --------------------------------------------------------------------------------------------

_CLICK_SENTINEL: Final = object()
_ISSUED: Final[weakref.WeakSet[OwnerClick]] = weakref.WeakSet()


@dataclass(frozen=True, eq=False, slots=True, weakref_slot=True)
class OwnerClick:
    """Proof that the authenticated owner tapped Publish on the go-live page just now.

    Only :func:`owner_click` builds one (a module-private sentinel); :func:`publish_post` also
    requires the very object it issued (copies are refused) and uses it once.
    """

    post_id: str
    approval_sha256: str
    _sentinel: object = field(repr=False)

    def __post_init__(self) -> None:
        if self._sentinel is not _CLICK_SENTINEL:
            raise TypeError("an OwnerClick comes only from the publish route's owner_click")


class PublishIn(ApiModel):
    """``POST /api/posts/{id}/publish``: the approval the owner saw and the page's token."""

    approval_sha256: Sha256Hex
    confirm_token: str = Field(min_length=1, max_length=200)


def _confirm_invalid() -> ProblemError:
    return ProblemError(
        409,
        code="confirm_invalid",
        detail="This publish page is out of date. Reload it and tap Publish again.",
    )


async def owner_click(
    post_id: str,
    body: PublishIn,
    principal: OwnerPrincipal,
    core: CoreSettingsDep,
    linkedin: LinkedInSettingsDep,
    clock: ClockDep,
) -> OwnerClick:
    """FastAPI dependency of the publish route: verifies ``body.confirmToken`` (409
    ``confirm_invalid``) and returns the click. ``principal`` makes the owner requirement
    explicit (core's middleware already refused everyone else and checked CSRF)."""
    del principal
    valid = verify_confirm_token(
        body.confirm_token,
        post_id,
        body.approval_sha256,
        now=clock.now(),
        key=confirm_key(core),
        ttl_seconds=linkedin.confirm_token_ttl_seconds,
    )
    if not valid:
        raise _confirm_invalid()
    click = OwnerClick(post_id, body.approval_sha256, _CLICK_SENTINEL)
    _ISSUED.add(click)
    return click


# --------------------------------------------------------------------------------------------
# Checks
# --------------------------------------------------------------------------------------------


def reconnect_problem(exc: ReconnectRequired) -> ProblemError:
    """409 ``reconnect_required`` (503 ``vault_unavailable`` when the vault is not configured)."""
    if exc.code == "vault_unavailable":
        return ProblemError(
            503,
            code="vault_unavailable",
            detail="The token vault is not configured on the server.",
            headers={"Retry-After": "5"},
        )
    return ProblemError(
        409,
        code="reconnect_required",
        detail="Reconnect LinkedIn in Settings > Integrations, then try again.",
        extensions={"reason": exc.code},
    )


def go_live_path(post: Post) -> str:
    """The go-live page of a post (push notifications open it)."""
    piece = post.article_id or post.id
    return f"/studio/{piece}/go-live?post={post.id}"


def _check_publishable(post: Post, click: OwnerClick, now: datetime) -> None:
    if post.status not in PUBLISHABLE_STATUSES:
        raise TransitionRefused("not_approved", "Only an approved post can be published.")
    if post.approval is None or not post.approval_current:
        raise TransitionRefused(
            "approval_stale", "The post changed after approval. Approve it again."
        )
    if post.approval.sha256 != click.approval_sha256:
        raise TransitionRefused(
            "approval_stale", "The approval changed since this page loaded. Reload it."
        )
    if post.expires_at is not None and parse_iso(post.expires_at) <= now:
        raise TransitionRefused("post_expired", "This time-sensitive post has expired.")


async def _screen(services: Services, post: Post, now: datetime) -> None:
    """The current blocklist over everything the post sends (``ConfidentialText``, 422, term
    ids only; ``BlocklistNotConfigured``, 409): a term added after the approval stops the tap
    before any upload or ``POST``."""
    blocklist, _ = await read_singleton(services.store, Blocklist, now=to_iso(now))
    check_texts(outgoing_texts(post), blocklist)


def _check_click(click: object, post_id: str) -> OwnerClick:
    if not isinstance(click, OwnerClick) or click not in _ISSUED:
        raise TypeError("publish_post needs the OwnerClick of the publish route")
    if click.post_id != post_id:
        raise TypeError("the click is for another post")
    _ISSUED.discard(click)  # one click, one attempt
    return click


# --------------------------------------------------------------------------------------------
# The attempt
# --------------------------------------------------------------------------------------------

_DETACHED: Final[set[asyncio.Task[Stored[Post]]]] = set()


def _live(post: Post, urn: str, stamp: str) -> Post:
    return revalidated(
        post,
        linkedin_urn=urn,
        posted_at=stamp,
        post_url=post_url_for(urn),
        final_text=post.display_text,
        last_error=None,
        updated_at=stamp,
    )


def _settled(post: Post, intent_id: str, outcome: CreateOutcome, stamp: str) -> Post | None:
    """The post after this attempt's answer, or ``None`` when there is nothing to change."""
    intent = post.intent
    if intent is None or intent.id != intent_id:
        return None  # not our attempt (cannot happen while the intent is ours)
    kind: PublishOutcome = outcome.outcome
    if post.status is PostStatus.PUBLISHING:
        settled = settle_publish_intent(
            post,
            kind,
            now=stamp,
            http_status=outcome.status,
            error_code=None if kind == "posted" else outcome.error_code,
        )
        if kind == "posted" and outcome.urn is not None:
            return _live(settled, outcome.urn, stamp)
        return settled
    if (
        kind == "posted"
        and outcome.urn is not None
        and post.status is PostStatus.NEEDS_ATTENTION
        and intent.status == "unknown"
    ):
        # The sweep settled the attempt as unknown first; LinkedIn's answer says it went out.
        target = POST_MACHINE.transition(post.status, PostStatus.POSTED)
        resolved = revalidated(
            intent, status="succeeded", resolved_at=stamp, http_status=outcome.status
        )
        moved = revalidated(post, status=target, status_changed_at=stamp, intent=resolved)
        return _live(moved, outcome.urn, stamp)
    return None


async def _settle(
    services: Services, post_id: str, intent_id: str, outcome: CreateOutcome
) -> Stored[Post]:
    stamp = to_iso(services.clock.now())
    for attempt in range(SETTLE_ATTEMPTS):
        try:
            return await update_doc(
                services.store,
                Post,
                post_id,
                lambda post: _settled(post, intent_id, outcome, stamp),
            )
        except StoreUnavailable:
            if attempt + 1 == SETTLE_ATTEMPTS:
                raise
            await asyncio.sleep(SETTLE_BACKOFF_SECONDS * (attempt + 1))
    raise AssertionError("unreachable")  # pragma: no cover


async def _create_once(
    client: LinkedInClient, token: str, body: dict[str, object]
) -> CreateOutcome:
    try:
        async with asyncio.timeout(CREATE_BUDGET_SECONDS):
            return await client.create_post(token, body)
    except TimeoutError:
        return CreateOutcome(status=None, urn=None, error_code="timeout")
    except Exception as exc:  # whatever went wrong, LinkedIn may have the post: unknown
        _logger.error("linkedin_create_crashed", extra={"error_type": type(exc).__name__})
        return CreateOutcome(status=None, urn=None, error_code="client_error")


async def _attempt(
    services: Services,
    client: LinkedInClient,
    post: Post,
    intent_id: str,
    token: str,
    body: dict[str, object],
) -> Stored[Post]:
    outcome = await _create_once(client, token, body)
    kind = outcome.outcome
    _logger.info(
        "linkedin_publish",
        extra={
            "post_id": post.id,
            "intent_id": intent_id,
            "outcome": kind,
            "status": outcome.status,
            "error_code": outcome.error_code,
            "linkedin_urn": outcome.urn,
        },
    )
    try:
        settled = await _settle(services, post.id, intent_id, outcome)
    except Exception:
        # The answer is in this log line (the URN is an id); the sweep moves the post to
        # needs_attention and the owner resolves it.
        _logger.error(
            "linkedin_publish_unsettled",
            extra={"post_id": post.id, "outcome": kind, "linkedin_urn": outcome.urn},
        )
        emit_alert("needs_attention", post.id)
        raise
    now = services.clock.now()
    if settled.doc.status is PostStatus.POSTED and settled.doc.posted_at is not None:
        await enqueue_post_live(services, post.id, settled.doc.posted_at)
    elif kind == "rejected" and outcome.status == 401:
        await mark_needs_reconnect(services.store, now=now, code="unauthorized")
    elif settled.doc.status is PostStatus.NEEDS_ATTENTION:
        emit_alert("needs_attention", post.id)
        await notify_owner(
            services.notifier,
            title="Check LinkedIn: did your post go out?",
            url=go_live_path(post),
            tag=f"publish:{post.id}",
        )
    return settled


def _detached_done(task: asyncio.Task[Stored[Post]]) -> None:
    _DETACHED.discard(task)
    if task.cancelled():
        return
    error = task.exception()  # retrieved here, so asyncio never reports it as lost
    if error is not None:
        _logger.error(
            "linkedin_publish_detached_failed", extra={"error_type": type(error).__name__}
        )


async def _shielded(task: asyncio.Task[Stored[Post]]) -> Stored[Post]:
    """Await the attempt; a cancelled request leaves it running to completion (it settles)."""
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        _DETACHED.add(task)  # a strong reference until it settles
        task.add_done_callback(_detached_done)
        raise


# --------------------------------------------------------------------------------------------
# The tap
# --------------------------------------------------------------------------------------------


async def publish_post(
    services: Services,
    post_id: str,
    *,
    click: OwnerClick,
    if_match: str,
    client: LinkedInClient | None = None,
) -> Stored[Post]:
    """The owner's tap (see the module docs). Returns the post after the attempt: read
    ``status`` (``posted``, ``needs_attention``, or ``approved`` with ``lastError``).

    Raises ``TypeError`` without the route's :class:`OwnerClick`; problems (409, 412, 503)
    through ``domain.deps.problems`` for refusals before the attempt. Never retries.
    """
    checked = _check_click(click, post_id)
    store = services.store
    linkedin = client if client is not None else current_client()
    if linkedin is None:
        raise ProblemError(503, code="linkedin_unavailable", headers={"Retry-After": "5"})
    stored = await read_doc(store, Post, post_id)
    if stored.etag != if_match:
        raise etag_mismatch(stored)  # a stale screen or a second tap: refuse, never re-apply
    now = services.clock.now()
    _check_publishable(stored.doc, checked, now)
    await _screen(services, stored.doc, now)
    try:
        token, member_urn = await access_token(services)
    except ReconnectRequired as exc:
        raise reconnect_problem(exc) from None
    if not media_ready(linkedin_media(stored.doc)):
        try:
            stored = await upload_post_media(services, post_id, client=linkedin)
        except ReconnectRequired as exc:
            raise reconnect_problem(exc) from None
        except UploadFailed as exc:
            raise ProblemError(
                409,
                code="media_upload_failed",
                detail="The media did not reach LinkedIn. Try again in a minute.",
                extensions={"reason": exc.code},
            ) from None
        now = services.clock.now()
        _check_publishable(stored.doc, checked, now)
    post = stored.doc
    try:
        body = post_body(post, member_urn)
    except PostBodyError as exc:
        raise TransitionRefused(exc.code, "This post cannot be sent to LinkedIn yet.") from None

    stamp = to_iso(now)
    approval = post.approval
    if approval is None:  # pragma: no cover - _check_publishable refused it
        raise TransitionRefused("approval_stale", "Approve the post again.")
    intent = PublishIntent(
        id=f"intent-{secrets.token_hex(12)}",
        created_at=stamp,
        approval_sha256=approval.sha256,
        status="pending",
    )
    target = POST_MACHINE.transition(post.status, PostStatus.PUBLISHING)
    publishing = revalidated(
        post,
        status=target,
        status_changed_at=stamp,
        updated_at=stamp,
        intent=intent,
        last_error=None,
    )
    try:
        written = await replace_doc(store, publishing, if_match=stored.etag)
    except PreconditionFailed:
        raise etag_mismatch() from None  # another tap won the CAS: it publishes, not this one
    task = asyncio.create_task(_attempt(services, linkedin, written.doc, intent.id, token, body))
    return await _shielded(task)
