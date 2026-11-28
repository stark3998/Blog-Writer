"""Service protocols (real implementations satisfy them), the registry and the shared fakes."""

from __future__ import annotations

import io
from datetime import timedelta

import pytest
from fastapi import FastAPI
from PIL import Image
from pydantic import BaseModel
from pypdf import PdfReader

from blogwriter.ai.policy import PolicyViolation
from blogwriter.ai.router import AIRouter, Finished
from blogwriter.ai.types import Message, Request, Sensitivity, Task, TextDelta, WebSearchTool
from blogwriter.core.egress import SafeHttpClient
from blogwriter.db import MemoryStore, Store
from blogwriter.domain.common import review_only_reasons
from blogwriter.domain.confidentiality import AiUseNotConfirmed
from blogwriter.domain.ports import (
    BlobExists,
    BlobNotFound,
    ConfidentialityClassifier,
    FetchRefused,
    HttpGetter,
    JobQueue,
    ModelRunner,
    ScheduleEntry,
    Services,
    ServiceUnavailable,
    StateServices,
    current_services,
    install_services,
    services_installed,
)
from blogwriter.domain.settings import BrandKit
from blogwriter.domain.testing import (
    SAMPLE_NOW,
    FakeClassifier,
    FakeServices,
    HashEmbedder,
    MemoryBlobStore,
    RecordingNotifier,
    ScriptedModelRunner,
    ScriptedReply,
    StaticHttp,
    StaticPageFetcher,
    StoreJobQueue,
    make_services,
)
from blogwriter.jobs import JobsService
from blogwriter.jobs.scheduler import daily, every, weekly
from blogwriter.notify.payload import NotificationPayload

# ---------------------------------------------------------------------------------------------
# Static conformance: pyright checks these assignments (the functions are never called).
# ---------------------------------------------------------------------------------------------


def _router_is_a_model_runner(router: AIRouter) -> ModelRunner:
    return router


def _jobs_service_is_a_job_queue(service: JobsService) -> JobQueue:
    return service


def _egress_client_is_an_http_getter(client: SafeHttpClient) -> HttpGetter:
    return client


def _fakes_are_services(fake: FakeServices, live: StateServices) -> tuple[Services, Services]:
    return fake, live


def _fake_classifier_is_a_classifier(fake: FakeClassifier) -> ConfidentialityClassifier:
    return fake


def test_conformance_functions_exist() -> None:
    assert callable(_router_is_a_model_runner)
    assert callable(_jobs_service_is_a_job_queue)
    assert callable(_egress_client_is_an_http_getter)
    assert callable(_fakes_are_services)
    assert callable(_fake_classifier_is_a_classifier)


# ---------------------------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------------------------


def test_services_registry() -> None:
    with pytest.raises(ServiceUnavailable):
        current_services()
    first = make_services()
    second = make_services()
    install_services(first)
    assert current_services() is first
    with services_installed(second):
        assert current_services() is second
    assert current_services() is first


def test_state_services_read_app_state() -> None:
    app = FastAPI()
    live = StateServices(app)
    with pytest.raises(ServiceUnavailable) as caught:
        _ = live.store
    assert caught.value.name == "store"
    assert live.ai is None
    assert live.blobs is None
    assert live.render is None
    assert live.classifier is None
    store = MemoryStore()
    app.state.store = store
    app.state.blobs = MemoryBlobStore()
    assert live.store is store
    assert isinstance(live.blobs, MemoryBlobStore)
    for name in ("jobs", "clock", "notifier", "egress"):
        with pytest.raises(ServiceUnavailable):
            getattr(live, name)


def test_schedule_entries_are_validated() -> None:
    entry = ScheduleEntry(
        id="sched-signals-crawl",
        name="Crawl feeds",
        job_type="signals.crawl",
        rule=every(6 * 3600),
        timezone="America/New_York",
        max_lateness=timedelta(hours=6),
    )
    assert entry.job_input is None
    for bad_id in ("signals-crawl", "sched-Signals", "sched-", "sched-a_b"):
        with pytest.raises(ValueError, match="sched-"):
            ScheduleEntry(id=bad_id, name="n", job_type="x", rule=daily("03:30"), timezone="UTC")
    with pytest.raises(ValueError, match="IANA"):
        ScheduleEntry(
            id="sched-a-b", name="n", job_type="x", rule=weekly(["sun"], "18:00"), timezone="Moon"
        )
    with pytest.raises(ValueError, match="negative"):
        ScheduleEntry(
            id="sched-a-b",
            name="n",
            job_type="x",
            rule=daily("03:30"),
            timezone="UTC",
            max_lateness=timedelta(seconds=-1),
        )


# ---------------------------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------------------------


class Outline(BaseModel):
    title: str
    sections: list[str]


def ask(
    sensitivity: Sensitivity, *, schema: type[BaseModel] | None = None, tools: bool = False
) -> Request:
    return Request(
        system="system",
        messages=(Message.user("hello"),),
        sensitivity=sensitivity,
        output_schema=schema,
        tools=(WebSearchTool(allowed_domains=("nist.gov",)),) if tools else (),
    )


async def test_scripted_runner_text_values_and_calls() -> None:
    runner = ScriptedModelRunner()
    runner.add(
        Task.OUTLINE, Outline(title="T", sections=["a", "b"]), '{"title": "U", "sections": []}'
    )
    first = await runner.run(
        Task.OUTLINE, ask(Sensitivity.ANONYMIZED, schema=Outline), origin="background"
    )
    assert first.output(Outline).title == "T"
    assert first.served_by.deployment == "fake-deployment"
    assert not first.review_only
    second = await runner.run(
        Task.OUTLINE, ask(Sensitivity.ANONYMIZED, schema=Outline), origin="owner", job_id="job-1"
    )
    assert second.output(Outline).title == "U"
    assert [call.origin for call in runner.calls_for(Task.OUTLINE)] == ["background", "owner"]
    assert runner.pending(Task.OUTLINE) == 0
    with pytest.raises(AssertionError, match="unexpected outline call"):
        await runner.run(Task.OUTLINE, ask(Sensitivity.PUBLIC), origin="owner")


async def test_scripted_runner_applies_the_sensitivity_policy() -> None:
    runner = ScriptedModelRunner()
    runner.add(Task.TRIAGE, "x")
    with pytest.raises(PolicyViolation):
        await runner.run(Task.TRIAGE, ask(Sensitivity.ANONYMIZED), origin="background")
    with pytest.raises(PolicyViolation):
        await runner.run(Task.DRAFT, ask(Sensitivity.PUBLIC, tools=True), origin="background")
    strict = ScriptedModelRunner(allow_private=False)
    with pytest.raises(PolicyViolation):
        await strict.run(Task.ANONYMIZE, ask(Sensitivity.PRIVATE), origin="owner")
    runner.add(
        Task.QUICK_SEARCH, ScriptedReply(text="found", search_sources=("https://nist.gov/a",))
    )
    found = await runner.run(Task.QUICK_SEARCH, ask(Sensitivity.PUBLIC, tools=True), origin="owner")
    assert found.completion.search_sources == ("https://nist.gov/a",)


async def test_scripted_runner_refusals_fallbacks_errors_and_streams() -> None:
    runner = ScriptedModelRunner()
    runner.add(
        Task.DRAFT,
        ScriptedReply(refused=True),
        ScriptedReply(text="Draft text here", fallback_reason="refusal"),
        ScriptedReply(error=TimeoutError()),
    )
    refused = await runner.run(Task.DRAFT, ask(Sensitivity.ANONYMIZED), origin="background")
    assert refused.refused
    assert refused.text == ""
    events = [
        event
        async for event in runner.stream(
            Task.DRAFT, ask(Sensitivity.ANONYMIZED), origin="background"
        )
    ]
    assert "".join(e.text for e in events if isinstance(e, TextDelta)) == "Draft text here"
    final = events[-1]
    assert isinstance(final, Finished)
    assert final.result.review_only
    # Like the router, a fallback after a refusal records the refusal it met.
    assert final.result.refusals
    assert review_only_reasons(final.result) == ("fallback_model", "refusal")
    assert review_only_reasons(refused) == ("refusal",)
    with pytest.raises(TimeoutError):
        await runner.run(Task.DRAFT, ask(Sensitivity.ANONYMIZED), origin="background")
    runner.add(Task.DRAFT, ScriptedReply(text="Clean draft"))
    clean = await runner.run(Task.DRAFT, ask(Sensitivity.ANONYMIZED), origin="background")
    assert clean.refusals == ()
    assert review_only_reasons(clean) == ()
    runner.add(Task.DRAFT, ScriptedReply(text="Primary down", fallback_reason="unavailable"))
    fallback = await runner.run(Task.DRAFT, ask(Sensitivity.ANONYMIZED), origin="background")
    assert fallback.refusals == ()
    assert review_only_reasons(fallback) == ("fallback_model",)


async def test_hash_embedder_similarity() -> None:
    embedder = HashEmbedder()
    a, b, c = await embedder.embed(
        ["agent identity tokens", "tokens for agent identity", "pasta recipes"], origin="background"
    )
    dot_ab = sum(x * y for x, y in zip(a, b, strict=True))
    dot_ac = sum(x * y for x, y in zip(a, c, strict=True))
    assert dot_ab > 0.8 > 0.2 > dot_ac
    assert embedder.dimensions == 64
    assert len(a) == 64


async def test_memory_blob_store() -> None:
    blobs = MemoryBlobStore()
    info = await blobs.put("media", "posts/p/x.png", b"abc", content_type="image/png")
    assert info.sha256 == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    with pytest.raises(BlobExists):
        await blobs.put("media", "posts/p/x.png", b"abc", content_type="image/png")
    await blobs.put("media", "posts/p/x.png", b"abcd", content_type="image/png", overwrite=True)
    assert await blobs.get("media", "posts/p/x.png") == b"abcd"
    assert await blobs.head("backups", "posts/p/x.png") is None
    assert [b.name for b in await blobs.list_blobs("media", "posts/")] == ["posts/p/x.png"]
    await blobs.delete("media", "posts/p/x.png")
    await blobs.delete("media", "posts/p/x.png")
    with pytest.raises(BlobNotFound):
        await blobs.get("media", "posts/p/x.png")
    with pytest.raises(ValueError, match="name"):
        await blobs.put("media", "../x", b"", content_type="image/png")


async def test_page_fetcher_enforces_allowed_urls() -> None:
    fetcher = StaticPageFetcher({"https://nist.gov/a": "passage text"})
    page = await fetcher.fetch("https://nist.gov/a", allowed_urls={"https://nist.gov/a"})
    assert page.text == "passage text"
    assert "passage text" not in repr(page)
    with pytest.raises(FetchRefused) as caught:
        await fetcher.fetch("https://nist.gov/a", allowed_urls=set())
    assert caught.value.reason == "url_not_allowed"
    with pytest.raises(FetchRefused):
        await fetcher.fetch("https://nist.gov/b", allowed_urls={"https://nist.gov/b"})


async def test_static_http() -> None:
    http = StaticHttp()
    http.add("https://example.com/feed", b"<rss/>", content_type="application/rss+xml")
    assert (await http.get("https://example.com/feed")).content == b"<rss/>"
    assert (await http.get("https://example.com/other")).status == 404
    assert http.requested == ["https://example.com/feed", "https://example.com/other"]


async def test_fake_render_service_returns_real_files() -> None:
    services = make_services()
    render = services.render
    assert render is not None
    brand = BrandKit.default(SAMPLE_NOW)
    slides: list[object] = [{"type": "cover"}, {"type": "statement"}, {"type": "recap"}]
    deck = await render.render_carousel({"slides": slides}, brand)
    assert deck.page_count == 3
    assert len(PdfReader(io.BytesIO(deck.pdf)).pages) == 3
    assert deck.cover_png == deck.pages[0]
    og = await render.render_og_image(title="T", subtitle=None, pillar_label="Agents", brand=brand)
    assert Image.open(io.BytesIO(og)).size == (1200, 630)
    assert (await render.render_mermaid_svg("graph TD; A-->B", brand)).startswith(b"<svg")
    png = await render.render_mermaid_png("graph TD; A-->B", 400)
    assert Image.open(io.BytesIO(png)).size == (400, 200)
    render.fail_with = RuntimeError("render down")
    with pytest.raises(RuntimeError):
        await render.render_image_card({}, brand)


async def test_notifier_and_job_queue_and_seeder() -> None:
    notifier = RecordingNotifier(deliver=False)
    result = await notifier.notify(NotificationPayload(title="T", url="/today", tag="t"))
    assert not result.any_delivered
    assert notifier.sent[0][1] == "normal"

    store: Store = MemoryStore()
    queue = StoreJobQueue(store)
    job = await queue.enqueue("planner.remind", {"reminderId": "rem-1"}, job_id="remind-rem-1")
    assert queue.types() == ["planner.remind"]
    assert (await queue.get(job.id)).status == "queued"
    assert (await queue.cancel(job.id)).status == "cancelled"
    assert (await queue.retry(job.id)).status == "queued"

    services = make_services()
    assert services.schedules is not None
    await services.schedules.reseed()
    assert services.schedules.reseeds == 1


async def test_fake_classifier() -> None:
    classifier = FakeClassifier(flag="Contoso")
    checks = await classifier.classify(["A clean story.", "The Contoso rollout."], origin="owner")
    assert [check.passed for check in checks] == [True, False]
    assert all(check.content_sha256 for check in checks)
    assert classifier.calls == [(("A clean story.", "The Contoso rollout."), "owner")]
    refusing = FakeClassifier(error=AiUseNotConfirmed())
    with pytest.raises(AiUseNotConfirmed):
        await refusing.classify(["x"], origin="background")
    assert make_services().classifier is not None


async def test_store_job_queue_can_refuse_unregistered_types() -> None:
    queue = StoreJobQueue(MemoryStore(), registered={"gates.run"})
    await queue.enqueue("gates.run", None)
    with pytest.raises(ValueError, match="not registered"):
        await queue.enqueue("posts.derive", None)
    assert queue.types() == ["gates.run"]


def test_make_services_overrides() -> None:
    store = MemoryStore()
    services = make_services(store=store, ai=None)
    assert services.store is store
    assert services.ai is None
    with pytest.raises(TypeError, match="no service"):
        make_services(nope=1)
