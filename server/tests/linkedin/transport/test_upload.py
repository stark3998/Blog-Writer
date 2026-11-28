"""Media uploads: URNs recorded before the PUT and re-attached on resume, polling until
AVAILABLE, the fixed-wait fallback, integrity checks, approval required."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from blogwriter.domain.common import MediaRef
from blogwriter.domain.docstore import Stored, create_doc, read_doc
from blogwriter.domain.posts import Post
from blogwriter.domain.states import ConnectionStatus, PostStatus
from blogwriter.domain.testing import FakeServices, pdf_bytes, png_bytes
from blogwriter.linkedin.transport.client import API_BASE, LinkedInClient
from blogwriter.linkedin.transport.testing import (
    DOC_URN,
    IMAGE_URNS,
    MEMBER_URN,
    UPLOAD_URL,
    FakeLinkedIn,
    approved_post,
    connect_member,
    fake_client,
    make_linkedin_settings,
    media_status_url,
    post_media,
)
from blogwriter.linkedin.transport.tokens import ReconnectRequired, read_connection
from blogwriter.linkedin.transport.upload import (
    POLL_INTERVAL_SECONDS,
    UNREADABLE_WAIT_SECONDS,
    UploadFailed,
    upload_post_media,
)

T0 = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)
DOC_INIT = f"{API_BASE}/documents?action=initializeUpload"
IMAGE_INIT = f"{API_BASE}/images?action=initializeUpload"
PDF = pdf_bytes(3)


class Sleeps:
    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


@pytest.fixture
async def ready(services: FakeServices, li_client: LinkedInClient) -> FakeServices:
    del li_client
    assert services.vault is not None
    await connect_member(services.store, services.vault, at=T0)
    return services


async def carousel(
    services: FakeServices, *, status: PostStatus = PostStatus.APPROVED
) -> Stored[Post]:
    assert services.blobs is not None
    pdf = await post_media(
        services.blobs,
        PDF,
        role="carousel_pdf",
        content_type="application/pdf",
        name="posts/p1/deck.pdf",
    )
    cover = await post_media(
        services.blobs,
        png_bytes(),
        role="carousel_cover",
        content_type="image/png",
        name="posts/p1/cover.png",
    )
    post = approved_post(
        display="Five checks before an agent gets a token.",
        lane="derivative",
        pillar="agents",
        format="carousel",
        document_title="Agent tokens: 5 checks",
        media=[cover, pdf],
        status=status,
    )
    return await create_doc(services.store, post)


def pdf_of(post: Post) -> MediaRef:
    (item,) = [item for item in post.media if item.role == "carousel_pdf"]
    return item


async def test_the_urn_is_recorded_before_the_put(
    ready: FakeServices, linkedin: FakeLinkedIn
) -> None:
    stored = await carousel(ready)
    linkedin.script(
        "POST",
        DOC_INIT,
        httpx.Response(200, json={"value": {"uploadUrl": UPLOAD_URL, "document": DOC_URN}}),
    )
    linkedin.script("PUT", UPLOAD_URL, httpx.ConnectError("reset"))  # the PUT never lands
    with pytest.raises(UploadFailed, match="network_error"):
        await upload_post_media(ready, stored.doc.id, sleep=Sleeps())
    after = await read_doc(ready.store, Post, stored.doc.id)
    item = pdf_of(after.doc)
    assert (item.linkedin_urn, item.linkedin_status) == (DOC_URN, "uploading")
    assert after.doc.approval_current  # URNs are not part of the approval hash
    (init,) = linkedin.calls("POST", DOC_INIT)
    assert linkedin.body(init) == {"initializeUploadRequest": {"owner": MEMBER_URN}}


async def test_a_resumed_upload_reattaches_instead_of_reissuing(
    ready: FakeServices, linkedin: FakeLinkedIn
) -> None:
    stored = await carousel(ready)
    linkedin.upload_flow("document", DOC_URN, "PROCESSING")
    linkedin.script("GET", media_status_url("document", DOC_URN), httpx.ReadTimeout("crash"))
    with pytest.raises(UploadFailed, match="timeout"):
        await upload_post_media(ready, stored.doc.id, sleep=Sleeps())  # dies while polling
    linkedin.script(
        "GET",
        media_status_url("document", DOC_URN),
        httpx.Response(200, json={"status": "PROCESSING"}),
    )
    linkedin.script(
        "GET",
        media_status_url("document", DOC_URN),
        httpx.Response(200, json={"status": "AVAILABLE"}),
    )
    resumed = await upload_post_media(ready, stored.doc.id, sleep=Sleeps())
    item = pdf_of(resumed.doc)
    assert (item.linkedin_urn, item.linkedin_status) == (DOC_URN, "available")
    assert len(linkedin.calls("POST", DOC_INIT)) == 1
    assert len(linkedin.calls("PUT", UPLOAD_URL)) == 1
    again = await upload_post_media(ready, stored.doc.id, sleep=Sleeps())
    assert again.etag == resumed.etag  # nothing left to do: no call, no write
    assert len(linkedin.requests) == 6


async def test_an_upload_linkedin_never_received_starts_over(
    ready: FakeServices, linkedin: FakeLinkedIn
) -> None:
    stored = await carousel(ready)
    linkedin.script(
        "POST",
        DOC_INIT,
        httpx.Response(200, json={"value": {"uploadUrl": UPLOAD_URL, "document": DOC_URN}}),
    )
    linkedin.script("PUT", UPLOAD_URL, httpx.ReadTimeout("lost"))
    with pytest.raises(UploadFailed):
        await upload_post_media(ready, stored.doc.id, sleep=Sleeps())
    linkedin.script(
        "GET",
        media_status_url("document", DOC_URN),
        httpx.Response(200, json={"status": "WAITING_UPLOAD"}),
    )
    fresh = "urn:li:document:D4E10AQHdoc2"
    linkedin.upload_flow("document", fresh)
    done = await upload_post_media(ready, stored.doc.id, sleep=Sleeps())
    assert (pdf_of(done.doc).linkedin_urn, pdf_of(done.doc).linkedin_status) == (fresh, "available")
    assert len(linkedin.calls("POST", DOC_INIT)) == 2
    assert linkedin.calls("PUT", UPLOAD_URL)[-1].content == PDF


async def test_polls_until_available(ready: FakeServices, linkedin: FakeLinkedIn) -> None:
    stored = await carousel(ready)
    linkedin.upload_flow("document", DOC_URN, "PROCESSING", "PROCESSING", "AVAILABLE")
    sleeps = Sleeps()
    done = await upload_post_media(ready, stored.doc.id, sleep=sleeps)
    assert pdf_of(done.doc).linkedin_status == "available"
    assert sleeps.calls == [POLL_INTERVAL_SECONDS, POLL_INTERVAL_SECONDS]
    put = linkedin.calls("PUT", UPLOAD_URL)[0]
    assert put.content == PDF
    assert put.headers["Content-Type"] == "application/pdf"


async def test_an_unreadable_status_falls_back_to_a_fixed_wait(
    ready: FakeServices, linkedin: FakeLinkedIn
) -> None:
    stored = await carousel(ready)
    linkedin.script(
        "POST",
        DOC_INIT,
        httpx.Response(200, json={"value": {"uploadUrl": UPLOAD_URL, "document": DOC_URN}}),
    )
    linkedin.script("PUT", UPLOAD_URL, httpx.Response(201))
    linkedin.script("GET", media_status_url("document", DOC_URN), httpx.Response(403))
    sleeps = Sleeps()
    done = await upload_post_media(ready, stored.doc.id, sleep=sleeps)
    assert pdf_of(done.doc).linkedin_status == "available"
    assert sleeps.calls == [UNREADABLE_WAIT_SECONDS]


async def test_processing_failures_and_timeouts(
    ready: FakeServices, linkedin: FakeLinkedIn
) -> None:
    stored = await carousel(ready)
    linkedin.upload_flow("document", DOC_URN, "PROCESSING_FAILED")
    with pytest.raises(UploadFailed, match="processing_failed"):
        await upload_post_media(ready, stored.doc.id, sleep=Sleeps())
    assert (
        pdf_of((await read_doc(ready.store, Post, stored.doc.id)).doc).linkedin_status == "failed"
    )
    slow = fake_client(linkedin, make_linkedin_settings(upload_poll_seconds=6))
    try:
        linkedin.upload_flow("document", DOC_URN, "PROCESSING", "PROCESSING", "PROCESSING")
        with pytest.raises(UploadFailed, match="upload_timeout"):
            await upload_post_media(ready, stored.doc.id, client=slow, sleep=Sleeps())
    finally:
        await slow.aclose()
    assert len(linkedin.calls("GET", media_status_url("document", DOC_URN))) == 3  # 1 + 2 polls


async def test_the_bytes_must_be_the_approved_bytes(
    ready: FakeServices, linkedin: FakeLinkedIn
) -> None:
    stored = await carousel(ready)
    assert ready.blobs is not None
    await ready.blobs.put(
        "media",
        "posts/p1/deck.pdf",
        b"%PDF-swapped",
        content_type="application/pdf",
        overwrite=True,
    )
    with pytest.raises(UploadFailed, match="media_integrity"):
        await upload_post_media(ready, stored.doc.id, sleep=Sleeps())
    await ready.blobs.delete("media", "posts/p1/deck.pdf")
    with pytest.raises(UploadFailed, match="blob_missing"):
        await upload_post_media(ready, stored.doc.id, sleep=Sleeps())
    assert linkedin.requests == []


@pytest.mark.parametrize("status", [PostStatus.DRAFT, PostStatus.KIT, PostStatus.POSTED])
async def test_only_approved_posts_upload(
    ready: FakeServices, linkedin: FakeLinkedIn, status: PostStatus
) -> None:
    stored = await carousel(ready, status=status)
    with pytest.raises(UploadFailed, match="not_approved"):
        await upload_post_media(ready, stored.doc.id, sleep=Sleeps())
    assert linkedin.requests == []


async def test_images_upload_in_order(ready: FakeServices, linkedin: FakeLinkedIn) -> None:
    assert ready.blobs is not None
    first = await post_media(
        ready.blobs,
        png_bytes(8, 8, "#111111"),
        role="image",
        content_type="image/png",
        name="posts/p2/a.png",
        alt_text="Before",
    )
    second = await post_media(
        ready.blobs,
        png_bytes(8, 8, "#222222"),
        role="image_card",
        content_type="image/png",
        name="posts/p2/b.png",
        alt_text="After",
    )
    post = approved_post(
        display="Before and after.",
        lane="native",
        pillar="identity",
        format="image_card",
        media=[first, second],
    )
    stored = await create_doc(ready.store, post)
    linkedin.upload_flow("image", IMAGE_URNS[0])
    linkedin.upload_flow("image", IMAGE_URNS[1])
    done = await upload_post_media(ready, stored.doc.id, sleep=Sleeps())
    assert [(m.linkedin_urn, m.linkedin_status) for m in done.doc.media] == [
        (IMAGE_URNS[0], "available"),
        (IMAGE_URNS[1], "available"),
    ]
    assert len(linkedin.calls("POST", IMAGE_INIT)) == 2


async def test_a_401_marks_the_connection_for_reconnect(
    ready: FakeServices, linkedin: FakeLinkedIn
) -> None:
    stored = await carousel(ready)
    linkedin.script("POST", DOC_INIT, httpx.Response(401))
    with pytest.raises(ReconnectRequired, match="unauthorized"):
        await upload_post_media(ready, stored.doc.id, sleep=Sleeps())
    conn = await read_connection(ready.store, ready.clock.now())
    assert (conn.status, conn.last_error) == (ConnectionStatus.NEEDS_RECONNECT, "unauthorized")


async def test_posts_without_media_never_call_linkedin(
    services: FakeServices, linkedin: FakeLinkedIn
) -> None:
    post = approved_post(display="Plain text.", lane="native", pillar="agents", format="text")
    stored = await create_doc(services.store, post)
    done = await upload_post_media(services, stored.doc.id, sleep=Sleeps())  # not even connected
    assert done.etag == stored.etag
    assert linkedin.requests == []
