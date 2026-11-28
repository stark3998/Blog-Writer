"""Editing and deleting a post that is live on LinkedIn (owner clicks on the go-live page).

Both need a confirm token (checked by the route against the post's approval), a ``posted``
post with a LinkedIn URN and a usable connection. LinkedIn is asked first, once (a POST is never
retried); the post follows with a write that applies while it is still the same live post.

**Edit.** The new display text must pass the post lint (blocking findings: 409 ``lint_failed``
with the findings) and the blocklist (422 ``confidential_text``, 409
``blocklist_not_configured``). The commentary is serialized from it with the post's hashtags
(:func:`~blogwriter.linkedin.littletext.api_commentary` of
:func:`~blogwriter.linkedin.littletext.parse_display`) and sent as a ``PARTIAL_UPDATE``. The
post records ``finalText`` (the live display text) and ``payload`` (the live commentary);
``displayText`` keeps the approved text the approval hash was taken over.

**Delete.** ``DELETE /rest/posts/{urn}`` (already gone counts as deleted); ``posted ->
deleted``.
"""

from __future__ import annotations

from collections.abc import Awaitable
from typing import Final

from blogwriter.core.logging import register_log_fields
from blogwriter.db import PreconditionFailed
from blogwriter.domain.base import to_iso
from blogwriter.domain.common import Finding, from_linkedin_finding
from blogwriter.domain.confidentiality import check_texts
from blogwriter.domain.docstore import Stored, read_doc, read_singleton, update_doc
from blogwriter.domain.ports import Services
from blogwriter.domain.posts import Post
from blogwriter.domain.settings import Blocklist
from blogwriter.domain.states import POST_MACHINE, PostStatus
from blogwriter.linkedin.lint import has_blocking, lint_post
from blogwriter.linkedin.littletext import LittleTextError, api_commentary, parse_display
from blogwriter.linkedin.transport.client import LinkedInClient, LinkedInError
from blogwriter.linkedin.transport.tokens import (
    ReconnectRequired,
    access_token,
    mark_needs_reconnect,
)
from blogwriter.linkedin.transport.updates import revalidated

__all__ = ["LiveActionRefused", "delete_live_post", "edit_live_post", "live_payload"]

register_log_fields("post_id")
_MAX_FINDINGS: Final = 50


class LiveActionRefused(Exception):
    """The live post cannot be edited or deleted (409 ``code``). ``findings`` (lint) carry
    spans into the new display text, never the text."""

    def __init__(self, code: str, detail: str, findings: list[Finding] | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail
        self.findings = findings or []


def live_payload(post: Post, display_text: str) -> str:
    """The commentary for ``display_text`` with the post's hashtags (``LittleTextError`` if it
    cannot be serialized)."""
    tokens = parse_display(display_text, post.hashtags)
    return api_commentary(tokens, allow_links=post.link_exception is not None)


async def _live_post(services: Services, post_id: str, if_match: str) -> tuple[Post, str]:
    stored = await read_doc(services.store, Post, post_id)
    if stored.etag != if_match:
        raise PreconditionFailed(f"post {post_id}: stale If-Match")
    post = stored.doc
    if post.status is not PostStatus.POSTED:
        raise LiveActionRefused("not_live", "Only a posted post can be changed on LinkedIn.")
    if post.linkedin_urn is None:
        raise LiveActionRefused(
            "no_linkedin_urn", "This post was posted by hand: change it in LinkedIn itself."
        )
    return post, post.linkedin_urn


async def _call(services: Services, call: Awaitable[None]) -> None:
    try:
        await call
    except LinkedInError as exc:
        if exc.unauthorized:
            await mark_needs_reconnect(
                services.store, now=services.clock.now(), code="unauthorized"
            )
            raise ReconnectRequired("unauthorized") from None
        raise


async def edit_live_post(
    services: Services,
    client: LinkedInClient,
    post_id: str,
    display_text: str,
    *,
    if_match: str,
) -> Stored[Post]:
    """Change a live post's text on LinkedIn, then on the post (see the module docs)."""
    post, urn = await _live_post(services, post_id, if_match)
    findings = lint_post(
        display_text,
        approved_hashtags=post.hashtags,
        allow_links=post.link_exception is not None,
    )
    if has_blocking(findings):
        converted = [from_linkedin_finding(finding) for finding in findings]
        raise LiveActionRefused(
            "lint_failed", "Fix the highlighted text first.", converted[:_MAX_FINDINGS]
        )
    now = services.clock.now()
    blocklist, _etag = await read_singleton(services.store, Blocklist, now=to_iso(now))
    check_texts({"displayText": display_text}, blocklist)
    try:
        payload = live_payload(post, display_text)
    except LittleTextError:
        raise LiveActionRefused("lint_failed", "Fix the highlighted text first.") from None
    token, _member = await access_token(services)
    await _call(services, client.update_commentary(token, urn, payload))
    stamp = to_iso(services.clock.now())

    def mutate(current: Post) -> Post | None:
        if current.status is not PostStatus.POSTED or current.linkedin_urn != urn:
            return None
        return revalidated(current, final_text=display_text, payload=payload, updated_at=stamp)

    return await update_doc(services.store, Post, post_id, mutate)


async def delete_live_post(
    services: Services, client: LinkedInClient, post_id: str, *, if_match: str
) -> Stored[Post]:
    """Delete a live post on LinkedIn, then mark it ``deleted``."""
    _post, urn = await _live_post(services, post_id, if_match)
    token, _member = await access_token(services)
    await _call(services, client.delete_post(token, urn))
    stamp = to_iso(services.clock.now())

    def mutate(current: Post) -> Post | None:
        if current.status is not PostStatus.POSTED or current.linkedin_urn != urn:
            return None
        return revalidated(
            current,
            status=POST_MACHINE.transition(current.status, PostStatus.DELETED),
            status_changed_at=stamp,
            updated_at=stamp,
        )

    return await update_doc(services.store, Post, post_id, mutate)
