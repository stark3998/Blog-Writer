"""``posts.render``: carousel PDF and cover, image card; the confidentiality gate on the exact
bytes (PDF metadata Title/Author only, text layer and alt texts against the blocklist); media on
an approved post invalidate the approval."""

from __future__ import annotations

import json

from blogwriter.domain.docstore import read_doc, upsert_doc
from blogwriter.domain.jobtypes import POSTS_RENDER, PostJobInput
from blogwriter.domain.posts import Approval, Post
from blogwriter.domain.states import PostStatus
from blogwriter.domain.testing import FakeServices, MemoryBlobStore, sample_document
from blogwriter.linkedin.posts.carousel import (
    CARD_FILENAME,
    COVER_FILENAME,
    PDF_FILENAME,
    pdf_problems,
    spec_json,
)
from blogwriter.linkedin.posts.serialize import serialize
from blogwriter.linkedin.posts.testing import (
    CompliantRenderService,
    carousel_slides,
    compliant_pdf,
    run_job,
    seed_settings,
)
from blogwriter.render.spec import CarouselSpec, ListSlide

TITLE = "The order that moved 2,000 apps"


def render_service(services: FakeServices) -> CompliantRenderService:
    assert isinstance(services.render, CompliantRenderService)
    return services.render


def blobs(services: FakeServices) -> MemoryBlobStore:
    assert services.blobs is not None
    return services.blobs


async def deck_post(services: FakeServices, **fields: object) -> Post:
    display, payload = serialize("Swipe for the order that worked.", [])
    spec = spec_json(CarouselSpec(title=TITLE, slides=carousel_slides(8)))
    values: dict[str, object] = {
        "display_text": display,
        "payload": payload,
        "format": "carousel",
        "carousel_spec": spec,
        "document_title": TITLE,
        **fields,
    }
    post = sample_document(Post, **values)
    await upsert_doc(services.store, post)
    return post


def test_pdf_problems() -> None:
    assert pdf_problems(compliant_pdf(2, title="Deck"), title="Deck", pages=2) == []
    producer = compliant_pdf(2, title="Deck", extra={"/Producer": "Chromium"})
    assert pdf_problems(producer, title="Deck", pages=2) == ["pdf_metadata"]
    assert pdf_problems(compliant_pdf(2, title="Other"), title="Deck", pages=2) == ["pdf_metadata"]
    assert pdf_problems(compliant_pdf(3, title="Deck"), title="Deck", pages=2) == ["pdf_pages"]
    assert pdf_problems(b"not a pdf", title="Deck", pages=1) == ["pdf_unreadable"]


async def test_a_carousel_renders_to_content_addressed_media(services: FakeServices) -> None:
    await seed_settings(services)
    post = await deck_post(services)
    job = await run_job(
        services, POSTS_RENDER, PostJobInput(post_id=post.id).to_json(), job_id="render-1"
    )
    assert job.status == "succeeded", job.error
    saved = (await read_doc(services.store, Post, post.id)).doc
    roles = [item.role for item in saved.media]
    assert roles == ["carousel_pdf", "carousel_cover"]
    pdf, cover = saved.media
    assert pdf.name == f"posts/{post.id}/{pdf.sha256}.pdf"
    assert cover.name == f"posts/{post.id}/{cover.sha256}.png"
    assert (pdf.filename, cover.filename) == (PDF_FILENAME, COVER_FILENAME)
    assert pdf.page_count == 8
    assert cover.alt_text == "We moved 2,000 apps in 14 months"
    assert ("media", pdf.name) in blobs(services).blobs
    assert render_service(services).calls == ["render_carousel"]
    assert saved.lint is not None  # alt texts linted with the new media
    assert "We moved 2,000 apps" not in json.dumps(job.to_body())  # references only
    # Rendering the same slides again changes nothing (same bytes, same names).
    again = await run_job(
        services, POSTS_RENDER, PostJobInput(post_id=post.id).to_json(), job_id="render-2"
    )
    assert again.status == "succeeded"
    assert (await read_doc(services.store, Post, post.id)).doc.media == saved.media


async def test_pdf_metadata_beyond_title_and_author_is_refused(services: FakeServices) -> None:
    await seed_settings(services)
    post = await deck_post(services)
    render_service(services).extra_metadata = {"/Creator": "HeadlessChrome"}
    job = await run_job(
        services, POSTS_RENDER, PostJobInput(post_id=post.id).to_json(), job_id="render-3"
    )
    assert job.status == "failed"
    assert job.error is not None
    assert job.error.code == "pdf_metadata"
    assert blobs(services).blobs == {}
    assert (await read_doc(services.store, Post, post.id)).doc.media == []


async def test_a_blocklisted_text_layer_is_refused(services: FakeServices) -> None:
    await seed_settings(services, terms=["Fabrikam"])
    post = await deck_post(services)
    render_service(services).text_layer = ["", "Diagram label: Fabrikam gateway"]
    job = await run_job(
        services, POSTS_RENDER, PostJobInput(post_id=post.id).to_json(), job_id="render-4"
    )
    assert job.status == "failed"
    assert job.error is not None
    assert job.error.code == "blocklist_hit"
    assert "Fabrikam" not in (job.error.detail or "")
    assert blobs(services).blobs == {}


async def test_an_unconfigured_blocklist_renders_nothing(services: FakeServices) -> None:
    await seed_settings(services, blocklist_configured=False)
    post = await deck_post(services)
    job = await run_job(
        services, POSTS_RENDER, PostJobInput(post_id=post.id).to_json(), job_id="render-5"
    )
    assert job.error is not None
    assert job.error.code == "blocklist_not_configured"
    assert render_service(services).calls == []


async def test_an_image_card(services: FakeServices) -> None:
    await seed_settings(services)
    spec = CarouselSpec(
        title="Order",
        slides=[
            ListSlide(title="Order of a migration", items=["Odd apps first", "Easy apps last"])
        ],
    )
    post = await deck_post(
        services, format="image_card", carousel_spec=spec_json(spec), document_title=None
    )
    job = await run_job(
        services, POSTS_RENDER, PostJobInput(post_id=post.id).to_json(), job_id="render-6"
    )
    assert job.status == "succeeded", job.error
    saved = (await read_doc(services.store, Post, post.id)).doc
    [card] = saved.media
    assert card.role == "image_card"
    assert card.filename == CARD_FILENAME
    assert (card.width, card.height) == (108, 135)
    assert card.alt_text == "Order of a migration. Odd apps first. Easy apps last"


async def test_new_media_invalidate_an_approval(services: FakeServices) -> None:
    await seed_settings(services)
    post = await deck_post(services, status=PostStatus.APPROVED)
    approved = post.model_copy(
        update={
            "approval": Approval(sha256=post.compute_approval_hash(), approved_at=post.created_at)
        }
    )
    await upsert_doc(services.store, approved)
    assert approved.approval_current
    job = await run_job(
        services, POSTS_RENDER, PostJobInput(post_id=post.id).to_json(), job_id="render-7"
    )
    assert job.status == "succeeded"
    saved = (await read_doc(services.store, Post, post.id)).doc
    assert saved.status is PostStatus.DRAFT
    assert saved.approval is None
    assert not saved.approval_current


async def test_render_without_storage_fails_as_not_configured(services: FakeServices) -> None:
    await seed_settings(services)
    post = await deck_post(services)
    services.blobs = None
    job = await run_job(
        services, POSTS_RENDER, PostJobInput(post_id=post.id).to_json(), job_id="render-8"
    )
    assert job.error is not None
    assert job.error.code == "storage_unavailable"
