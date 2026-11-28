"""What goes to LinkedIn: the ``/rest/posts`` body (goldens), media selection and post URLs.

The goldens in ``goldens/post_bodies.json`` change only deliberately: they are the exact bodies
the publisher sends (the Phase 0 spike's shape) for each format.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from blogwriter.domain.common import MediaRef
from blogwriter.domain.posts import Post
from blogwriter.linkedin.littletext import unescape
from blogwriter.linkedin.transport.content import (
    PostBodyError,
    content_problem,
    is_linkedin_url,
    linkedin_media,
    post_body,
    post_url_for,
    urn_from_post_url,
)
from blogwriter.linkedin.transport.testing import (
    DOC_URN,
    IMAGE_URNS,
    MEMBER_URN,
    POST_URN,
    approved_post,
)
from blogwriter.linkedin.transport.updates import revalidated

GOLDENS = Path(__file__).parent / "goldens" / "post_bodies.json"
TEXT = (
    "We scope every token to one tool call (C# and snake_case_names).\n\n"
    "How: 3 steps | {no} @mentions [x] (y) <z> ~ * _ \\ #AISecurity"
)


def media(
    role: str,
    name: str,
    content_type: str,
    sha: str,
    alt: str | None = None,
    urn: str | None = None,
) -> MediaRef:
    return MediaRef.model_validate(
        {
            "role": role,
            "container": "media",
            "name": name,
            "sha256": sha * 64,
            "content_type": content_type,
            "size_bytes": 10,
            "alt_text": alt,
            "linkedin_urn": urn,
            "linkedin_status": "available" if urn else None,
        }
    )


def cases() -> dict[str, Post]:
    return {
        "text": approved_post(
            display=TEXT, hashtags=["AISecurity"], lane="native", pillar="agents", format="text"
        ),
        "carousel": approved_post(
            display="Five checks before an agent gets a token.",
            lane="derivative",
            pillar="agents",
            format="carousel",
            document_title="Agent tokens: 5 checks",
            media=[
                media("carousel_cover", "posts/p/cover.png", "image/png", "a"),
                media("carousel_pdf", "posts/p/deck.pdf", "application/pdf", "b", urn=DOC_URN),
            ],
        ),
        "image": approved_post(
            display="One diagram, one decision.",
            lane="native",
            pillar="identity",
            format="image_card",
            media=[
                media(
                    "image_card",
                    "posts/p/card.png",
                    "image/png",
                    "c",
                    alt="A flow from user to agent to tool, each hop with its own token.",
                    urn=IMAGE_URNS[0],
                )
            ],
        ),
        "multi_image": approved_post(
            display="Before and after.",
            lane="native",
            pillar="identity",
            format="image_card",
            media=[
                media(
                    "image",
                    "posts/p/a.jpg",
                    "image/jpeg",
                    "d",
                    alt="Before: one shared token.",
                    urn=IMAGE_URNS[0],
                ),
                media("upload", "posts/p/b.png", "image/png", "e", urn=IMAGE_URNS[1]),
            ],
        ),
        "poll": approved_post(
            display="Quick poll for agent builders.",
            lane="native",
            pillar="agents",
            format="poll",
            poll={
                "question": "How do your agents authenticate to tools?",
                "options": [
                    "Shared service account",
                    {"text": "Per-user OAuth"},
                    "Per-call tokens",
                ],
                "duration": "SEVEN_DAYS",
            },
        ),
    }


def test_post_bodies_match_the_goldens() -> None:
    golden: dict[str, Any] = json.loads(GOLDENS.read_text(encoding="utf-8"))
    bodies = {name: post_body(post, MEMBER_URN) for name, post in cases().items()}
    assert bodies == golden


def test_the_commentary_is_the_approved_payload() -> None:
    post = cases()["text"]
    body = post_body(post, MEMBER_URN)
    assert body["commentary"] == post.payload
    assert unescape(post.payload) == post.display_text  # reserved characters escaped, tag not
    assert "#AISecurity" in post.payload
    assert "\\#AISecurity" not in post.payload


def test_the_body_is_bound_by_the_approval_hash() -> None:
    """Every change to what the body carries (except the author and the media URNs that
    LinkedIn gave the hashed bytes) changes the approval hash."""
    base = cases()["carousel"]
    pdf = base.media[1]
    changes: dict[str, dict[str, object]] = {
        "payload": {"payload": "Something else entirely."},
        "title": {"document_title": "Agent tokens: 6 checks"},
        "media bytes": {"media": [base.media[0], revalidated(pdf, sha256="f" * 64)]},
        "alt text": {"media": [base.media[0], revalidated(pdf, alt_text="Slides")]},
        "format": {"format": "text"},
        "poll": {"poll": {"question": "Q?", "options": ["a", "b"]}},
    }
    for name, update in changes.items():
        changed = revalidated(base, **update)
        assert changed.compute_approval_hash() != base.compute_approval_hash(), name
    same = revalidated(base, media=[base.media[0], revalidated(pdf, linkedin_urn=None)])
    assert same.compute_approval_hash() == base.compute_approval_hash()


def test_a_body_needs_media_available_on_linkedin() -> None:
    post = cases()["carousel"]
    waiting = revalidated(post.media[1], linkedin_status="uploading")
    pending = revalidated(post, media=[post.media[0], waiting])
    with pytest.raises(PostBodyError, match="media_not_uploaded"):
        post_body(pending, MEMBER_URN)
    assert content_problem(pending) is None  # uploading is not a shape problem


@pytest.mark.parametrize(
    ("name", "update", "code"),
    [
        ("carousel", {"media": []}, "media_missing"),
        ("carousel", {"document_title": None}, "document_title_missing"),
        ("image", {"media": [media("image", "x/a.webp", "image/webp", "a")]}, "media_missing"),
        (
            "image",
            {"media": [media("image", f"x/{n}.png", "image/png", "a") for n in range(21)]},
            "too_many_images",
        ),
        ("poll", {"poll": {"question": "Q?", "options": ["only one"]}}, "poll_invalid"),
        (
            "poll",
            {"poll": {"question": "Q?", "options": ["a", "b"], "duration": "1d"}},
            "poll_invalid",
        ),
        ("poll", {"poll": {"question": "Q?", "options": ["a", "b" * 31]}}, "poll_invalid"),
        ("poll", {"poll": None}, "poll_invalid"),
    ],
)
def test_shape_problems(name: str, update: dict[str, object], code: str) -> None:
    post = revalidated(cases()[name], **update)
    assert content_problem(post) == code
    with pytest.raises(PostBodyError, match=code):
        post_body(post, MEMBER_URN)


def test_linkedin_media_picks_what_each_format_sends() -> None:
    all_cases = cases()
    carousel = linkedin_media(all_cases["carousel"])
    assert [item.role for item in carousel] == ["carousel_pdf"]  # the cover PNG stays home
    assert [item.name for item in linkedin_media(all_cases["multi_image"])] == [
        "posts/p/a.jpg",
        "posts/p/b.png",
    ]
    assert linkedin_media(all_cases["text"]) == []
    assert linkedin_media(all_cases["poll"]) == []


def test_post_urls() -> None:
    assert post_url_for(POST_URN) == f"https://www.linkedin.com/feed/update/{POST_URN}/"
    with pytest.raises(ValueError, match="URN"):
        post_url_for("urn:li:person:abc")
    assert urn_from_post_url(post_url_for(POST_URN)) == POST_URN
    encoded = "https://www.linkedin.com/feed/update/urn%3Ali%3AugcPost%3A7311/"
    assert urn_from_post_url(encoded) == "urn:li:ugcPost:7311"
    assert urn_from_post_url("https://www.linkedin.com/feed/update/urn:li:activity:7311/") is None
    assert is_linkedin_url("https://www.linkedin.com/posts/someone_activity-7311-abc")
    assert is_linkedin_url("https://linkedin.com/feed/")
    for bad in (
        "http://www.linkedin.com/feed/",
        "https://evil.test/www.linkedin.com",
        "https://linkedin.com.evil.test/",
        "https://user@www.linkedin.com/",
    ):
        assert not is_linkedin_url(bad)
