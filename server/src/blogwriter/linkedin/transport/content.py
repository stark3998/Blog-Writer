"""What goes to LinkedIn for a post: the media to upload, the ``/rest/posts`` body, post URLs.

:func:`post_body` is built only from what :func:`blogwriter.domain.hashing.approval_hash`
covers (display text and payload, document title, media by role/SHA-256/alt text, format, poll,
link exception) plus the media URNs LinkedIn gave those exact bytes, so an approved hash binds
the whole body except the author. ``commentary`` is ``post.payload``, the escaped little text
linkedin posts serialized and the approval covers; it is never re-serialized here.

Media per format (:func:`linkedin_media`):

``carousel``
    The ``carousel_pdf`` document (exactly one): ``{"media": {"title", "id"}}``.
``image_card``
    The ``image_card``/``image``/``upload`` PNG or JPEG images, in order: one is
    ``{"media": {"id", "altText"}}``, 2-20 are ``{"multiImage": {"images": [...]}}``.
``poll``
    No media: ``{"poll": {"question", "options", "settings": {"duration"}}}`` from ``post.poll``.
``text``
    No ``content`` at all.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Final, cast
from urllib.parse import unquote, urlsplit

from blogwriter.domain.common import MediaRef
from blogwriter.domain.posts import Post
from blogwriter.linkedin.transport.client import MediaKind

__all__ = [
    "IMAGE_CONTENT_TYPES",
    "IMAGE_ROLES",
    "MAX_IMAGES",
    "POLL_DURATIONS",
    "PostBodyError",
    "content_problem",
    "is_linkedin_url",
    "linkedin_media",
    "media_kind",
    "media_ready",
    "post_body",
    "post_url_for",
    "urn_from_post_url",
]

IMAGE_ROLES: Final = ("image_card", "image", "upload")
IMAGE_CONTENT_TYPES: Final = frozenset({"image/png", "image/jpeg"})
"""What LinkedIn's Images API takes among the media types we store (not WebP or SVG)."""
MAX_IMAGES: Final = 20
POLL_DURATIONS: Final = ("ONE_DAY", "THREE_DAYS", "SEVEN_DAYS", "FOURTEEN_DAYS")
MAX_POLL_QUESTION: Final = 140
MAX_POLL_OPTION: Final = 30
FEED_UPDATE_URL: Final = "https://www.linkedin.com/feed/update/"
_POST_URN_RE: Final = re.compile(r"urn:li:(?:share|ugcPost):[0-9]{1,30}")
_LINKEDIN_HOSTS: Final = frozenset({"linkedin.com", "www.linkedin.com"})


class PostBodyError(ValueError):
    """The post cannot be expressed as a LinkedIn body. ``code``: ``media_missing``,
    ``media_not_uploaded``, ``too_many_images``, ``document_title_missing``, ``poll_invalid``.
    """

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def media_kind(item: MediaRef) -> MediaKind:
    """``document`` for the carousel PDF, ``image`` for everything else we upload."""
    return "document" if item.role == "carousel_pdf" else "image"


def linkedin_media(post: Post) -> list[MediaRef]:
    """The media of ``post`` that go to LinkedIn, in order (see the module docs)."""
    match post.format:
        case "carousel":
            return [
                item
                for item in post.media
                if item.role == "carousel_pdf" and item.content_type == "application/pdf"
            ]
        case "image_card":
            return [
                item
                for item in post.media
                if item.role in IMAGE_ROLES and item.content_type in IMAGE_CONTENT_TYPES
            ]
        case "text" | "poll":
            return []


_PLACEHOLDER_URN: Final = "urn:li:placeholder:0"


def _uploaded_urn(item: MediaRef, *, uploaded: bool = True) -> str:
    if not uploaded:
        return _PLACEHOLDER_URN
    if item.linkedin_status != "available" or not item.linkedin_urn:
        raise PostBodyError("media_not_uploaded")
    return item.linkedin_urn


def _image_entry(item: MediaRef, *, uploaded: bool) -> dict[str, object]:
    entry: dict[str, object] = {"id": _uploaded_urn(item, uploaded=uploaded)}
    if item.alt_text:
        entry["altText"] = item.alt_text
    return entry


def _poll_content(poll: Mapping[str, object] | None) -> dict[str, object]:
    if poll is None:
        raise PostBodyError("poll_invalid")
    question = poll.get("question")
    raw_options = poll.get("options")
    duration = poll.get("duration", "THREE_DAYS")
    if not isinstance(question, str) or not 1 <= len(question.strip()) <= MAX_POLL_QUESTION:
        raise PostBodyError("poll_invalid")
    if not isinstance(raw_options, list):
        raise PostBodyError("poll_invalid")
    options: list[dict[str, object]] = []
    for raw in cast(list[object], raw_options):
        text: object = raw
        if isinstance(raw, dict):
            text = cast(dict[str, object], raw).get("text")
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= MAX_POLL_OPTION:
            raise PostBodyError("poll_invalid")
        options.append({"text": text.strip()})
    if not 2 <= len(options) <= 4 or duration not in POLL_DURATIONS:
        raise PostBodyError("poll_invalid")
    return {
        "question": question.strip(),
        "options": options,
        "settings": {"duration": duration},
    }


def _content(post: Post, *, uploaded: bool = True) -> dict[str, object] | None:
    media = linkedin_media(post)
    match post.format:
        case "text":
            return None
        case "poll":
            return {"poll": _poll_content(post.poll)}
        case "carousel":
            if len(media) != 1:
                raise PostBodyError("media_missing")
            if not post.document_title:
                raise PostBodyError("document_title_missing")
            document = _uploaded_urn(media[0], uploaded=uploaded)
            return {"media": {"title": post.document_title, "id": document}}
        case "image_card":
            if not media:
                raise PostBodyError("media_missing")
            if len(media) > MAX_IMAGES:
                raise PostBodyError("too_many_images")
            if len(media) == 1:
                return {"media": _image_entry(media[0], uploaded=uploaded)}
            images = [_image_entry(item, uploaded=uploaded) for item in media]
            return {"multiImage": {"images": images}}


def content_problem(post: Post) -> str | None:
    """Why ``post`` cannot become a LinkedIn body whatever its upload state (a
    :class:`PostBodyError` code), or ``None``."""
    try:
        _content(post, uploaded=False)
    except PostBodyError as exc:
        return exc.code
    return None


def post_body(post: Post, member_urn: str) -> dict[str, object]:
    """The ``POST /rest/posts`` body for ``post`` by ``member_urn`` (see the module docs).

    Raises :class:`PostBodyError` when media is missing or not ``available`` on LinkedIn yet,
    or the poll is not one LinkedIn takes.
    """
    body: dict[str, object] = {
        "author": member_urn,
        "commentary": post.payload,
        "visibility": "PUBLIC",
        "distribution": {
            "feedDistribution": "MAIN_FEED",
            "targetEntities": [],
            "thirdPartyDistributionChannels": [],
        },
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False,
    }
    content = _content(post)
    if content is not None:
        body["content"] = content
    return body


def media_ready(items: Sequence[MediaRef]) -> bool:
    """Every item is uploaded and ``available`` on LinkedIn."""
    return all(item.linkedin_status == "available" and item.linkedin_urn for item in items)


def post_url_for(urn: str) -> str:
    """The public URL of a LinkedIn post: ``https://www.linkedin.com/feed/update/<urn>/``."""
    if not _POST_URN_RE.fullmatch(urn):
        raise ValueError("not a LinkedIn post URN")
    return f"{FEED_UPDATE_URL}{urn}/"


def is_linkedin_url(url: str) -> bool:
    """An ``https`` URL on ``linkedin.com`` (what the owner may paste as a post's URL)."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    return (
        parts.scheme == "https"
        and (host in _LINKEDIN_HOSTS or host.endswith(".linkedin.com"))
        and parts.username is None
        and parts.password is None
    )


def urn_from_post_url(url: str) -> str | None:
    """The ``share``/``ugcPost`` URN inside a LinkedIn post URL, if it carries one (activity
    URLs do not)."""
    match = _POST_URN_RE.search(unquote(urlsplit(url).path))
    return match.group(0) if match else None
