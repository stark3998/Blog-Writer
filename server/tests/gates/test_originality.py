"""Gate 4: the verbatim budget, slop over threshold, specifics, embedding novelty."""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from blogwriter.ai.ledger import Origin
from blogwriter.db import MemoryStore
from blogwriter.domain.articles import Article, PublishState
from blogwriter.domain.base import sha256_hex
from blogwriter.domain.common import Finding, Quote
from blogwriter.domain.docstore import create_doc, read_doc
from blogwriter.domain.settings import EmployerPolicy, ModelPreferences
from blogwriter.domain.testing import SAMPLE_NOW, FakeSlopChecker, HashEmbedder, sample_document
from blogwriter.gates.originality import (
    SLOP_BUDGET_MIN,
    embedding_input,
    novelty,
    passage_verbatim_findings,
    slop_budget_findings,
    slop_findings,
    verbatim_findings,
)
from blogwriter.gates.slop import TextMeasure
from blogwriter.gates.testing import SAMPLE_BODY, SAMPLE_CITATION, SAMPLE_META, sample_brief
from blogwriter.gates.text import markdown_view

QUOTE = (
    "Organizations should plan the attribute mapping before they migrate applications, test "
    "provisioning with a pilot group, and keep a rollback path for every wave of the move."
)
POLICY = EmployerPolicy.default(SAMPLE_NOW).model_copy(update={"ai_use_confirmed": True})
PREFERENCES = ModelPreferences.default(SAMPLE_NOW)
ORIGIN: Origin = "owner"


def brief_with_quote() -> object:
    return sample_brief(quotes=[Quote(id="q1", citation_id=SAMPLE_CITATION, text=QUOTE)])


def test_one_quote_fits_the_budget_and_repeating_it_does_not() -> None:
    brief = sample_brief(quotes=[Quote(id="q1", citation_id=SAMPLE_CITATION, text=QUOTE)])
    once = f"As the guide puts it, “{QUOTE}” That matched our plan.\n"
    assert len(QUOTE) <= 200
    assert verbatim_findings(once, brief) == []
    twice = f"{once}\nAgain: {QUOTE.upper()}\n"
    findings = verbatim_findings(twice, brief)
    assert [(f.code, f.severity, f.overridable, f.ref) for f in findings] == [
        ("verbatim-budget", "block", False, SAMPLE_CITATION)
    ]
    assert twice[findings[0].start or 0 :].startswith("Organizations")
    assert verbatim_findings(twice, None) == []


def test_passage_counts_add_sources_the_quotes_did_not_flag() -> None:
    brief = sample_brief(quotes=[Quote(id="q1", citation_id=SAMPLE_CITATION, text=QUOTE)])
    findings = passage_verbatim_findings({"src-other": 260, "src-small": 120}, body="", brief=brief)
    assert [(f.code, f.ref, f.overridable) for f in findings] == [
        ("verbatim-budget", "src-other", False)
    ]
    twice = f"{QUOTE}\n\n{QUOTE}\n"
    assert passage_verbatim_findings({SAMPLE_CITATION: 400}, body=twice, brief=brief) == []


def structural(count: int, code: str = "colon-reveal") -> list[Finding]:
    return [
        Finding(code=code, severity="warn", message="x", start=n, end=n + 1, overridable=True)
        for n in range(count)
    ]


def test_slop_over_the_budget_blocks_overridably() -> None:
    long_text = TextMeasure(words=2000, specifics=200)
    assert slop_budget_findings(structural(6), long_text) == []  # budget 6.0
    over = slop_budget_findings(structural(7), long_text)
    assert [(f.code, f.severity, f.overridable) for f in over] == [
        ("slop-over-threshold", "block", True)
    ]
    short = TextMeasure(words=300, specifics=30)
    assert slop_budget_findings(structural(int(SLOP_BUDGET_MIN)), short) == []
    tells = structural(8, "ai-tell")  # half weight: 4.0 > 3.0
    assert [f.code for f in slop_budget_findings(tells, short)] == ["slop-over-threshold"]
    ignored = structural(9, "low-specificity")
    assert slop_budget_findings(ignored, short) == []


def test_too_few_specifics_block_overridably() -> None:
    vague = slop_budget_findings([], TextMeasure(words=500, specifics=5))
    assert [(f.code, f.overridable) for f in vague] == [("specifics-below-minimum", True)]
    assert "1.0 specifics per 100 words" in vague[0].message
    assert slop_budget_findings([], TextMeasure(words=500, specifics=10)) == []
    assert slop_budget_findings([], TextMeasure(words=40, specifics=0)) == []


def test_any_slop_checker_gets_the_masked_body() -> None:
    fake = FakeSlopChecker(structural(1))
    view = markdown_view(SAMPLE_BODY)
    found, counts = slop_findings(view, fake)
    assert found == structural(1)
    assert counts.words > 100
    assert fake.checked == [view.text]
    assert len(fake.checked[0]) == len(SAMPLE_BODY)


def live(markdown: str, **fields: object) -> Article:
    values: dict[str, object] = {
        "status": "published",
        "meta": SAMPLE_META,
        "markdown": markdown,
        "version": 1,
        "publish": PublishState(live=True, last_published_at=SAMPLE_NOW),
    }
    values.update(fields)
    return sample_document(Article, **values)


async def run_novelty(
    article: Article,
    store: MemoryStore,
    embedder: HashEmbedder | None,
    *,
    policy: EmployerPolicy = POLICY,
) -> tuple[list[Finding], list[float] | None, float | None]:
    result = await novelty(
        article,
        store=store,
        embedder=embedder,
        policy=policy,
        preferences=PREFERENCES,
        corpus_size=50,
        origin=ORIGIN,
        job_id="job-1",
    )
    return result.findings, result.embedding, result.max_similarity


def codes(findings: Sequence[Finding]) -> list[str]:
    return [finding.code for finding in findings]


async def test_no_embedder_fails_closed() -> None:
    findings, embedding, _ = await run_novelty(live(SAMPLE_BODY), MemoryStore(), None)
    assert [(f.code, f.severity, f.overridable) for f in findings] == [
        ("novelty-unavailable", "block", True)
    ]
    assert embedding is None


async def test_novelty_needs_the_ai_use_confirmation() -> None:
    embedder = HashEmbedder()
    unconfirmed = EmployerPolicy.default(SAMPLE_NOW)
    findings, _, _ = await run_novelty(
        live(SAMPLE_BODY), MemoryStore(), embedder, policy=unconfirmed
    )
    assert codes(findings) == ["novelty-unavailable"]
    assert embedder.calls == []


async def test_a_near_copy_of_a_live_article_fails_and_its_embedding_is_backfilled() -> None:
    store = MemoryStore()
    earlier = live(SAMPLE_BODY)
    unrelated = live("Agents need their own identities. Tokens per tool call, never per agent.\n")
    await create_doc(store, earlier)
    await create_doc(store, unrelated)
    draft = sample_document(Article, meta=SAMPLE_META, markdown=SAMPLE_BODY, version=2)
    embedder = HashEmbedder()
    findings, embedding, similarity = await run_novelty(draft, store, embedder)
    assert [(f.code, f.ref) for f in findings] == [("novelty-too-similar", earlier.id)]
    assert similarity == pytest.approx(1.0)
    assert embedding is not None
    assert len(embedding) == embedder.dimensions
    stored = (await read_doc(store, Article, earlier.id)).doc
    assert stored.embedding is not None
    assert stored.embedding_sha256 == sha256_hex(embedding_input(stored))
    # Next time the stored vector is reused: only the draft is embedded.
    embedder.calls.clear()
    await run_novelty(draft, store, embedder)
    assert embedder.calls == [(embedding_input(draft),)]


async def test_a_new_angle_passes_and_the_article_never_compares_with_itself() -> None:
    store = MemoryStore()
    published = live(SAMPLE_BODY)
    await create_doc(store, published)
    correction = published.model_copy(update={"status": "in_review", "version": 2})
    findings, _, similarity = await run_novelty(correction, store, HashEmbedder())
    assert findings == []
    assert similarity is None
    other = sample_document(
        Article, meta=SAMPLE_META, markdown="Agents need scoped tokens per tool call.\n"
    )
    findings, _, similarity = await run_novelty(other, store, HashEmbedder())
    assert findings == []
    assert similarity is not None
    assert similarity < PREFERENCES.novelty_max_similarity


class BrokenEmbedder(HashEmbedder):
    def __init__(self, *, wrong_size: bool = False) -> None:
        super().__init__()
        self.wrong_size = wrong_size

    async def embed(
        self, texts: Sequence[str], *, origin: Origin, job_id: str | None = None
    ) -> list[list[float]]:
        if self.wrong_size:
            return [[1.0] for _ in texts]
        raise RuntimeError("provider down")


@pytest.mark.parametrize("wrong_size", [False, True])
async def test_embedding_failures_never_pass(wrong_size: bool) -> None:
    findings, embedding, _ = await run_novelty(
        live(SAMPLE_BODY), MemoryStore(), BrokenEmbedder(wrong_size=wrong_size)
    )
    assert codes(findings) == ["novelty-unavailable"]
    assert embedding is None


async def test_an_unreadable_corpus_fails_closed() -> None:
    store = MemoryStore()
    bad = live(SAMPLE_BODY).to_document()
    bad["version"] = "not a number"
    await store.create("articles", bad)
    findings, _, _ = await run_novelty(live("Other text.\n"), store, HashEmbedder())
    assert codes(findings) == ["novelty-unavailable"]
