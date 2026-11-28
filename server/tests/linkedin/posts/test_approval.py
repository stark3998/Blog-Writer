"""The approval is bound to the SHA-256 of text plus media (hash v2): any change of the text,
the title, a media item, an alt text, the format, the poll or the link exception makes it stale,
so an edit after approval can never ship unnoticed."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from blogwriter.domain.common import MediaRef
from blogwriter.domain.posts import Approval, LinkException, Post
from blogwriter.domain.testing import SAMPLE_NOW, sample_document
from blogwriter.linkedin.posts.serialize import serialize

PDF = MediaRef(
    role="carousel_pdf",
    name="posts/p/" + "a" * 64 + ".pdf",
    sha256="a" * 64,
    content_type="application/pdf",
    size_bytes=100,
)
COVER = MediaRef(
    role="carousel_cover",
    name="posts/p/" + "b" * 64 + ".png",
    sha256="b" * 64,
    content_type="image/png",
    size_bytes=100,
    alt_text="We moved 2,000 apps",
)


def approved() -> Post:
    display, payload = serialize("We moved 2,000 apps in 14 months.", [])
    post = sample_document(
        Post,
        format="carousel",
        display_text=display,
        payload=payload,
        document_title="Deck",
        media=[PDF, COVER],
    )
    return post.model_copy(
        update={"approval": Approval(sha256=post.compute_approval_hash(), approved_at=SAMPLE_NOW)}
    )


def _text(post: Post) -> Post:
    display, payload = serialize(post.display_text + " Edited.", [])
    return post.model_copy(update={"display_text": display, "payload": payload})


CHANGES: dict[str, Callable[[Post], Post]] = {
    "text": _text,
    "title": lambda p: p.model_copy(update={"document_title": "Another deck"}),
    "media": lambda p: p.model_copy(
        update={"media": [PDF.model_copy(update={"sha256": "c" * 64}), COVER]}
    ),
    "media order": lambda p: p.model_copy(update={"media": [COVER, PDF]}),
    "alt text": lambda p: p.model_copy(
        update={"media": [PDF, COVER.model_copy(update={"alt_text": "Other"})]}
    ),
    "format": lambda p: p.model_copy(update={"format": "image_card"}),
    "poll": lambda p: p.model_copy(update={"poll": {"question": "Q?", "options": ["A", "B"]}}),
    "link exception": lambda p: p.model_copy(
        update={
            "link_exception": LinkException(
                url="https://example.org/r", reason="Link experiment", approved_at=SAMPLE_NOW
            )
        }
    ),
}


def test_an_untouched_approval_is_current() -> None:
    assert approved().approval_current


@pytest.mark.parametrize("change", sorted(CHANGES))
def test_any_change_makes_the_approval_stale(change: str) -> None:
    post = approved()
    assert not CHANGES[change](post).approval_current


def test_fields_outside_the_published_body_do_not_touch_the_approval() -> None:
    post = approved()
    moved = post.model_copy(update={"pillar": "identity", "last_error": "x", "review_only": True})
    assert moved.approval_current
