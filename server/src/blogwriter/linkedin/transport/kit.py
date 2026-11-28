"""Go-live without the API: the ready-to-post kit, the go-live mode and resolving an attempt.

**The kit** (``post.status == kit``) is the owner posting by hand: copy the display text, the
document title and alt texts, download the media, post or schedule natively, then tell the app.
Its states are ``prepared -> native_draft_saved -> natively_scheduled -> posted``
(:data:`~blogwriter.domain.states.KIT_MACHINE`); a new native time ``T`` while scheduled is an
update, not a transition. ``posted`` also moves the post to ``posted`` with the URL and the
**final text** the owner confirmed (prefilled with the display text). The kit copies the
display text, never the escaped payload.

**The mode** switches an approved or staged post into the kit (``kit.state = prepared``) and a
kit that was only prepared back to one-click (``approved``).

**Resolving** a ``needs_attention`` publish attempt: the owner checked LinkedIn and says whether
the post went out (``posted``) or not (back to ``approved``, or ``draft`` if the approval no
longer holds).

Every write is a CAS with the owner's If-Match (``replace_doc``).
"""

from __future__ import annotations

from datetime import datetime
from typing import Final, Literal

from blogwriter.db import Store
from blogwriter.domain.api import ApiModel
from blogwriter.domain.base import parse_iso, to_iso
from blogwriter.domain.common import MediaRef
from blogwriter.domain.docstore import Stored, read_doc, replace_doc
from blogwriter.domain.posts import GoLiveMode, KitInfo, Post
from blogwriter.domain.states import (
    KIT_MACHINE,
    POST_MACHINE,
    KitState,
    PostStatus,
    TransitionRefused,
)
from blogwriter.linkedin.transport.content import (
    is_linkedin_url,
    linkedin_media,
    post_url_for,
    urn_from_post_url,
)
from blogwriter.linkedin.transport.updates import revalidated

__all__ = [
    "OPEN_LINKEDIN_URL",
    "KitDownload",
    "KitView",
    "kit_checklist",
    "kit_transition",
    "kit_view",
    "resolve_attention",
    "set_go_live_mode",
]

OPEN_LINKEDIN_URL: Final = "https://www.linkedin.com/feed/"
"""Where "Open LinkedIn" goes (a prefilled composer link waits for the Phase 0 checks)."""
_EXTENSIONS: Final = {
    "application/pdf": "pdf",
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
    "image/svg+xml": "svg",
}


def _validated(post: Post, stamp: str, **changes: object) -> Post:
    return revalidated(post, **changes, updated_at=stamp)


def _status(post: Post, target: PostStatus, stamp: str) -> dict[str, object]:
    """``status``/``status_changed_at`` for ``target`` (``TransitionError`` when refused)."""
    return {"status": POST_MACHINE.transition(post.status, target), "status_changed_at": stamp}


def _checked_post_url(url: str | None) -> str | None:
    if url is None:
        return None
    if not is_linkedin_url(url):
        raise TransitionRefused("post_url_invalid", "Paste the post's linkedin.com link.")
    return url


# --------------------------------------------------------------------------------------------
# Kit states
# --------------------------------------------------------------------------------------------


async def kit_transition(
    store: Store,
    post_id: str,
    to: KitState,
    *,
    if_match: str,
    now: datetime,
    scheduled_at: str | None = None,
    post_url: str | None = None,
    final_text: str | None = None,
) -> Stored[Post]:
    """Move a kit post to ``to`` (see the module docs).

    ``natively_scheduled`` needs ``scheduled_at`` (again while scheduled: the new ``T``);
    ``posted`` needs ``post_url`` (a linkedin.com URL) and ``final_text``. Raises
    ``TransitionError`` (409 ``invalid_transition``), ``TransitionRefused`` (409),
    ``ValueError`` for a missing field and ``PreconditionFailed`` (412) for a stale If-Match.
    """
    stored = await read_doc(store, Post, post_id)
    post = stored.doc
    stamp = to_iso(now)
    if post.status is not PostStatus.KIT or post.kit is None:
        raise TransitionRefused("not_in_kit", "Switch the post to the kit first.")
    kit = post.kit
    changes: dict[str, object] = {}
    kit_changes: dict[str, object] = {}
    if to is KitState.NATIVELY_SCHEDULED:
        if scheduled_at is None:
            raise ValueError("natively_scheduled needs scheduled_at")
        parse_iso(scheduled_at)
        if kit.state is not KitState.NATIVELY_SCHEDULED:
            kit_changes["state"] = KIT_MACHINE.transition(kit.state, to)
        kit_changes["scheduled_at"] = scheduled_at
    elif to is KitState.NATIVE_DRAFT_SAVED:
        kit_changes["state"] = KIT_MACHINE.transition(kit.state, to)
        kit_changes["draft_saved_at"] = stamp
    elif to is KitState.POSTED:
        if post_url is None or final_text is None or not final_text.strip():
            raise ValueError("posted needs post_url and final_text")
        url = _checked_post_url(post_url)
        kit_changes["state"] = KIT_MACHINE.transition(kit.state, to)
        went_live = stamp
        if kit.scheduled_at is not None and parse_iso(kit.scheduled_at) <= now:
            went_live = kit.scheduled_at  # scheduled natively at T, and T has passed
        kit_changes["posted_at"] = went_live
        changes.update(_status(post, PostStatus.POSTED, stamp))
        urn = urn_from_post_url(url) if url is not None else None
        changes.update(
            posted_at=went_live,
            post_url=url,
            linkedin_urn=urn,
            final_text=final_text,
            last_error=None,
        )
    else:
        raise TransitionRefused("invalid_kit_state", "A kit starts prepared; pick another state.")
    new_kit = revalidated(kit, **kit_changes)
    updated = _validated(post, stamp, kit=new_kit, **changes)
    return await replace_doc(store, updated, if_match=if_match)


# --------------------------------------------------------------------------------------------
# Go-live mode
# --------------------------------------------------------------------------------------------


async def set_go_live_mode(
    store: Store, post_id: str, mode: GoLiveMode, *, if_match: str, now: datetime
) -> Stored[Post]:
    """``kit``: approved/staged -> kit (``prepared``). ``one_click``: a kit that is still
    ``prepared`` -> approved. Asking for the mode a post already has changes nothing."""
    stored = await read_doc(store, Post, post_id)
    post = stored.doc
    stamp = to_iso(now)
    if mode == "kit":
        if post.status is PostStatus.KIT:
            return stored
        if post.status not in (PostStatus.APPROVED, PostStatus.STAGED):
            raise TransitionRefused("not_approved", "Only an approved post can use the kit.")
        if not post.approval_current:
            raise TransitionRefused("approval_stale", "Approve the post again first.")
        updated = _validated(
            post,
            stamp,
            **_status(post, PostStatus.KIT, stamp),
            go_live_mode="kit",
            kit=KitInfo(state=KitState.PREPARED, prepared_at=stamp),
        )
        return await replace_doc(store, updated, if_match=if_match)
    if post.status in (PostStatus.APPROVED, PostStatus.STAGED):
        if post.go_live_mode == "one_click":
            return stored
        updated = _validated(post, stamp, go_live_mode="one_click")
        return await replace_doc(store, updated, if_match=if_match)
    if post.status is PostStatus.KIT:
        if post.kit is not None and post.kit.state is not KitState.PREPARED:
            raise TransitionRefused(
                "kit_in_progress",
                "The post is already saved or scheduled in LinkedIn. Finish it in the kit.",
            )
        if not post.approval_current:
            raise TransitionRefused("approval_stale", "Approve the post again first.")
        updated = _validated(
            post,
            stamp,
            **_status(post, PostStatus.APPROVED, stamp),
            go_live_mode="one_click",
            kit=None,
        )
        return await replace_doc(store, updated, if_match=if_match)
    raise TransitionRefused("not_approved", "Only an approved post can change how it goes live.")


# --------------------------------------------------------------------------------------------
# needs_attention
# --------------------------------------------------------------------------------------------


async def resolve_attention(
    store: Store,
    post_id: str,
    outcome: Literal["posted", "not_posted"],
    *,
    if_match: str,
    now: datetime,
    post_url: str | None = None,
) -> Stored[Post]:
    """Settle a publish attempt whose answer never arrived, from what the owner saw on
    LinkedIn. Only for ``needs_attention`` posts with an ``unknown`` intent."""
    stored = await read_doc(store, Post, post_id)
    post = stored.doc
    stamp = to_iso(now)
    intent = post.intent
    if post.status is not PostStatus.NEEDS_ATTENTION or intent is None:
        raise TransitionRefused("not_a_publish_attempt", "Nothing is waiting for your check.")
    if intent.status != "unknown":
        raise TransitionRefused("not_a_publish_attempt", "Nothing is waiting for your check.")
    if outcome == "posted":
        url = _checked_post_url(post_url)
        urn = urn_from_post_url(url) if url is not None else None
        if url is None and urn is not None:
            url = post_url_for(urn)
        updated = _validated(
            post,
            stamp,
            **_status(post, PostStatus.POSTED, stamp),
            posted_at=intent.created_at,
            post_url=url,
            linkedin_urn=urn,
            final_text=post.display_text,
            last_error=None,
        )
    else:
        target = PostStatus.APPROVED if post.approval_current else PostStatus.DRAFT
        updated = _validated(post, stamp, **_status(post, target, stamp), last_error=None)
    return await replace_doc(store, updated, if_match=if_match)


# --------------------------------------------------------------------------------------------
# The kit view
# --------------------------------------------------------------------------------------------


class KitDownload(ApiModel):
    media: MediaRef
    url: str
    filename: str


class KitView(ApiModel):
    """``GET /api/posts/{id}/kit``: what the owner copies and downloads, and the 4 steps."""

    display_text: str
    document_title: str | None = None
    alt_texts: list[str | None]
    downloads: list[KitDownload]
    checklist: list[str]
    open_linked_in_url: str = OPEN_LINKEDIN_URL
    state: KitState | None = None
    scheduled_at: str | None = None


def _filename(post: Post, item: MediaRef, index: int, count: int) -> str:
    if item.filename:
        return item.filename
    ext = _EXTENSIONS.get(item.content_type, "bin")
    suffix = f"-{index + 1}" if count > 1 else ""
    return f"{post.id}{suffix}.{ext}"


def kit_checklist(post: Post) -> list[str]:
    """The four steps, worded for the post's format."""
    match post.format:
        case "carousel":
            attach = "Add a document, upload the PDF and paste the document title."
        case "image_card":
            attach = "Add the image(s) and paste each alt text into LinkedIn's alt text box."
        case "poll":
            attach = "Add a poll with the question and options from the composer."
        case "text":
            attach = "Leave the post text-only: no link preview, no attachment."
    return [
        "Start a post in LinkedIn and paste the text exactly as copied.",
        attach,
        "Check the first three lines in the preview before you post or schedule.",
        "Post it or schedule it in LinkedIn, then mark it here with the post's link.",
    ]


def kit_view(post: Post) -> KitView:
    """The kit for ``post``: the display text verbatim (never the escaped payload)."""
    media = linkedin_media(post)
    downloads = [
        KitDownload(media=item, url=item.url, filename=_filename(post, item, index, len(media)))
        for index, item in enumerate(media)
        if item.container == "media"
    ]
    return KitView(
        display_text=post.display_text,
        document_title=post.document_title,
        alt_texts=[item.alt_text for item in media],
        downloads=downloads,
        checklist=kit_checklist(post),
        state=post.kit.state if post.kit is not None else None,
        scheduled_at=post.kit.scheduled_at if post.kit is not None else None,
    )
