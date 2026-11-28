"""Gate 4, originality: verbatim budget, slop, specifics, embedding novelty.

- **Verbatim budget (hard).** Text copied from any one source totals at most
  :data:`~blogwriter.domain.common.MAX_QUOTE_CHARS` (200) characters across the piece: every
  run of :data:`QUOTE_RUN_CHARS` or more characters the body shares with one of the brief's
  quotes of a source counts, every time it appears (case and whitespace insensitive). When the
  fact check re-fetched a source, the run lengths shared with the passage (in memory only; only
  the counts reach this gate) count as well.
- **Slop over threshold (overridable).** The slop detectors' structural findings, plus AI tells
  at half weight, against a budget of :data:`SLOP_BUDGET_PER_1000_WORDS` per 1,000 words (at
  least :data:`SLOP_BUDGET_MIN`). The individual findings are shown as warnings.
- **Minimum density of specifics (overridable).** Fewer than
  ``ARTICLE_THRESHOLDS.specifics_per_100_words`` specifics per 100 words.
- **Embedding novelty (overridable).** Cosine similarity of the article's embedding against the
  last ``BW_GATES_NOVELTY_CORPUS`` live articles; at or above
  ``ModelPreferences.noveltyMaxSimilarity`` fails. No embedder, no AI-use confirmation or an
  unreadable corpus is ``novelty-unavailable``: the gate fails (never passes). Corpus articles
  without a current embedding are embedded in the same batch and their ``embedding`` field is
  back-filled (gates owns it).

==============================  =========  =======================================================
code                            blocks     when
==============================  =========  =======================================================
``verbatim-budget``             hard       one source's verbatim text over 200 characters
``slop-over-threshold``         yes        slop score above the budget
``specifics-below-minimum``     yes        too few specifics
``novelty-too-similar``         yes        too close to a live article (``ref``: its id)
``novelty-unavailable``         yes        the novelty check could not run
slop codes                      no         :mod:`blogwriter.gates.slop`
==============================  =========  =======================================================
"""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from blogwriter.ai.ledger import BrakeEngaged, Origin
from blogwriter.core.logging import describe_exception, register_log_fields
from blogwriter.db import Filter, OrderBy, Store, StoreError
from blogwriter.domain.articles import MAX_EMBEDDING_DIMENSIONS, Article
from blogwriter.domain.base import sha256_hex
from blogwriter.domain.common import MAX_QUOTE_CHARS, Finding
from blogwriter.domain.confidentiality import AiUseNotConfirmed, require_ai_use
from blogwriter.domain.docstore import find_docs, update_doc
from blogwriter.domain.jobflow import JobDeferred
from blogwriter.domain.ports import Embedder, SlopChecker
from blogwriter.domain.research import Brief
from blogwriter.domain.settings import EmployerPolicy, ModelPreferences
from blogwriter.gates.slop import (
    AI_TELL,
    ARTICLE_THRESHOLDS,
    STRUCTURAL_CODES,
    SlopDetectors,
    TextMeasure,
    measure,
)
from blogwriter.gates.text import TextView, fold, markdown_view

__all__ = [
    "AI_TELL_WEIGHT",
    "EMBED_BATCH",
    "EMBED_MAX_CHARS",
    "QUOTE_RUN_CHARS",
    "SLOP_BUDGET_MIN",
    "SLOP_BUDGET_PER_1000_WORDS",
    "NoveltyResult",
    "embedding_input",
    "novelty",
    "passage_verbatim_findings",
    "slop_budget_findings",
    "slop_findings",
    "verbatim_counts",
    "verbatim_findings",
]

QUOTE_RUN_CHARS: Final = 20
SLOP_BUDGET_PER_1000_WORDS: Final = 3.0
SLOP_BUDGET_MIN: Final = 3.0
AI_TELL_WEIGHT: Final = 0.5
EMBED_MAX_CHARS: Final = 24_000
"""About 6,000 tokens: under the embedding model's 8,192-token input limit."""
EMBED_BATCH: Final = 16

_logger: Final = logging.getLogger("blogwriter.gates.originality")
register_log_fields("article_id", "corpus_size", "backfilled")


# --------------------------------------------------------------------------------------------
# Verbatim budget
# --------------------------------------------------------------------------------------------


def verbatim_counts(
    body: str, brief: Brief | None
) -> dict[str, tuple[int, tuple[int, int] | None]]:
    """Per citation id: verbatim characters in ``body`` from that source's quotes, and the
    first run's span. One pass over the body (case and whitespace insensitive, like
    ``domain.research.verbatim_spans``); CPU-bound: call it in a thread for long bodies."""
    if brief is None or not brief.quotes:
        return {}
    grams: dict[str, set[str]] = {}
    for quote in brief.quotes:
        folded_quote = fold(quote.text)[0]
        for index in range(len(folded_quote) - QUOTE_RUN_CHARS + 1):
            grams.setdefault(folded_quote[index : index + QUOTE_RUN_CHARS], set()).add(
                quote.citation_id
            )
    if not grams:
        return {}
    folded_body, offsets = fold(body)
    spans_by_source: dict[str, list[tuple[int, int]]] = {}
    for index in range(len(folded_body) - QUOTE_RUN_CHARS + 1):
        owners = grams.get(folded_body[index : index + QUOTE_RUN_CHARS])
        if not owners:
            continue
        span = (offsets[index], offsets[index + QUOTE_RUN_CHARS - 1] + 1)
        for citation_id in owners:
            spans_by_source.setdefault(citation_id, []).append(span)
    counts: dict[str, tuple[int, tuple[int, int] | None]] = {}
    for citation_id, spans in spans_by_source.items():
        merged = _merge(spans)
        total = sum(end - start for start, end in merged)
        counts[citation_id] = (total, merged[0] if merged else None)
    return counts


def _merge(spans: Sequence[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _verbatim_finding(citation_id: str, chars: int, span: tuple[int, int] | None) -> Finding:
    return Finding(
        code="verbatim-budget",
        severity="block",
        message=(
            f"Text copied from source [^{citation_id}] totals {chars} characters; the limit is "
            f"{MAX_QUOTE_CHARS} per source across the piece. Paraphrase and cite."
        )[:500],
        start=None if span is None else span[0],
        end=None if span is None else span[1],
        ref=citation_id,
        overridable=False,
    )


def verbatim_findings(body: str, brief: Brief | None) -> list[Finding]:
    """Sources whose quotes appear verbatim for more than 200 characters in total."""
    return [
        _verbatim_finding(citation_id, chars, span)
        for citation_id, (chars, span) in sorted(verbatim_counts(body, brief).items())
        if chars > MAX_QUOTE_CHARS
    ]


def passage_verbatim_findings(
    passage_counts: Mapping[str, int], *, body: str, brief: Brief | None
) -> list[Finding]:
    """The same budget against the re-fetched passages (counts from the fact check), for the
    sources the quotes alone did not already put over it."""
    quoted = verbatim_counts(body, brief)
    findings: list[Finding] = []
    for citation_id, chars in sorted(passage_counts.items()):
        already = quoted.get(citation_id, (0, None))[0] > MAX_QUOTE_CHARS
        if chars > MAX_QUOTE_CHARS and not already:
            findings.append(_verbatim_finding(citation_id, chars, None))
    return findings


# --------------------------------------------------------------------------------------------
# Slop and specifics
# --------------------------------------------------------------------------------------------


def slop_findings(view: TextView, slop: SlopChecker) -> tuple[list[Finding], TextMeasure]:
    """The slop checker's findings for an article body's prose view (spans index the body),
    and the view's word and specifics counts (gate 4 measures them itself, whatever checker
    is installed)."""
    if isinstance(slop, SlopDetectors):
        return slop.analyze(view, ARTICLE_THRESHOLDS)
    return slop.check(view.text), measure(view)


def slop_budget_findings(findings: Sequence[Finding], counts: TextMeasure) -> list[Finding]:
    """The gate-level blockers: slop over budget, specifics under the minimum."""
    out: list[Finding] = []
    score = sum(
        1.0 if f.code in STRUCTURAL_CODES else AI_TELL_WEIGHT if f.code == AI_TELL else 0.0
        for f in findings
    )
    budget = max(SLOP_BUDGET_MIN, SLOP_BUDGET_PER_1000_WORDS * counts.words / 1000.0)
    if score > budget:
        out.append(
            Finding(
                code="slop-over-threshold",
                severity="block",
                message=(
                    f"Slop score {score:g} for {counts.words} words (budget {budget:.1f}). "
                    "Rewrite the flagged constructions."
                ),
                overridable=True,
            )
        )
    minimum = ARTICLE_THRESHOLDS.specifics_per_100_words
    if (
        counts.words >= ARTICLE_THRESHOLDS.specifics_min_words
        and counts.specifics_per_100_words < minimum
    ):
        out.append(
            Finding(
                code="specifics-below-minimum",
                severity="block",
                message=(
                    f"{counts.specifics_per_100_words:.1f} specifics per 100 words (minimum "
                    f"{minimum:g}): add numbers, names, tools and first-hand detail."
                ),
                overridable=True,
            )
        )
    return out


# --------------------------------------------------------------------------------------------
# Embedding novelty
# --------------------------------------------------------------------------------------------


def embedding_input(article: Article) -> str:
    """What gets embedded: title, excerpt and the body's prose, whitespace collapsed."""
    prose = " ".join(markdown_view(article.markdown).text.split())
    head = " ".join(part for part in (article.meta.title, article.meta.excerpt) if part)
    text = f"{head}\n\n{prose}" if head else prose
    return text[:EMBED_MAX_CHARS]


@dataclass(frozen=True, slots=True)
class NoveltyResult:
    findings: list[Finding]
    embedding: list[float] | None
    max_similarity: float | None = None


def _unavailable(message: str) -> NoveltyResult:
    return NoveltyResult(
        findings=[
            Finding(
                code="novelty-unavailable",
                severity="block",
                message=message,
                overridable=True,
            )
        ],
        embedding=None,
    )


def _cosine(first: Sequence[float], second: Sequence[float]) -> float:
    if len(first) != len(second) or not first:
        return 0.0
    dot = sum(a * b for a, b in zip(first, second, strict=True))
    norm = math.sqrt(sum(a * a for a in first)) * math.sqrt(sum(b * b for b in second))
    return dot / norm if norm else 0.0


def _inputs(docs: Sequence[Article]) -> dict[str, tuple[str, str]]:
    """``id -> (embedding input, its SHA-256)`` (CPU-bound: runs in a thread)."""
    out: dict[str, tuple[str, str]] = {}
    for doc in docs:
        text = embedding_input(doc)
        out[doc.id] = (text, sha256_hex(text))
    return out


def _valid_embedding(article: Article, dimensions: int, input_sha256: str) -> bool:
    vector = article.embedding
    return (
        vector is not None
        and len(vector) == dimensions
        and article.embedding_sha256 == input_sha256
    )


async def novelty(
    article: Article,
    *,
    store: Store,
    embedder: Embedder | None,
    policy: EmployerPolicy,
    preferences: ModelPreferences,
    corpus_size: int,
    origin: Origin,
    job_id: str | None,
) -> NoveltyResult:
    """The novelty check (callers wrap it in ``defer_on_brake``)."""
    if embedder is None:
        return _unavailable(
            "The novelty check cannot run: embeddings are not served yet. Review by hand."
        )
    try:
        require_ai_use(policy)
    except AiUseNotConfirmed:
        return _unavailable(
            "The novelty check cannot run until you confirm the employer AI-use policy."
        )
    try:
        corpus = await find_docs(
            store,
            Article,
            [Filter("publish.live", "==", True)],
            order_by=OrderBy("publish.lastPublishedAt", descending=True),
            limit=corpus_size + 1,
            strict=True,
        )
    except StoreError:
        return _unavailable("The novelty check cannot read the published articles.")
    others = [stored.doc for stored in corpus if stored.doc.id != article.id][:corpus_size]
    dimensions = embedder.dimensions
    if not 1 <= dimensions <= MAX_EMBEDDING_DIMENSIONS:
        return _unavailable("The embedder's dimensions are not supported.")
    inputs = await asyncio.to_thread(_inputs, [article, *others])
    missing = [doc for doc in others if not _valid_embedding(doc, dimensions, inputs[doc.id][1])]
    texts = [inputs[article.id][0], *(inputs[doc.id][0] for doc in missing)]
    try:
        vectors: list[list[float]] = []
        for first in range(0, len(texts), EMBED_BATCH):
            batch = texts[first : first + EMBED_BATCH]
            vectors.extend(await embedder.embed(batch, origin=origin, job_id=job_id))
    except (JobDeferred, BrakeEngaged):
        raise
    except Exception as exc:  # fail closed: no vectors, no pass
        _logger.warning(
            "gates_embed_failed", extra={"article_id": article.id, **describe_exception(exc)}
        )
        return _unavailable("The novelty check failed to embed the text. Run the gates again.")
    if len(vectors) != len(texts) or any(len(vector) != dimensions for vector in vectors):
        return _unavailable("The embedder returned an unexpected result.")
    own = [float(value) for value in vectors[0]]
    fresh = dict(zip((doc.id for doc in missing), vectors[1:], strict=True))
    best_id: str | None = None
    best = -1.0
    for doc in others:
        vector = fresh.get(doc.id) or doc.embedding or []
        similarity = _cosine(own, vector)
        if similarity > best:
            best, best_id = similarity, doc.id
    findings: list[Finding] = []
    if best_id is not None and best >= preferences.novelty_max_similarity:
        findings.append(
            Finding(
                code="novelty-too-similar",
                severity="block",
                message=(
                    f"Too close to a published article (similarity {best:.2f}, limit "
                    f"{preferences.novelty_max_similarity:.2f}). Find a new angle."
                ),
                ref=best_id,
                overridable=True,
            )
        )
    backfilled = await _backfill(store, missing, fresh, inputs)
    _logger.info(
        "gates_novelty",
        extra={"article_id": article.id, "corpus_size": len(others), "backfilled": backfilled},
    )
    return NoveltyResult(
        findings=findings, embedding=own, max_similarity=None if best_id is None else best
    )


async def _backfill(
    store: Store,
    docs: Sequence[Article],
    vectors: Mapping[str, list[float]],
    inputs: Mapping[str, tuple[str, str]],
) -> int:
    """Store fresh embeddings of corpus articles (best effort; only while their content is the
    content that was embedded: same meta and body, so the same embedding input)."""
    written = 0
    for doc in docs:
        vector = vectors.get(doc.id)
        if vector is None:
            continue
        content = doc.compute_sha256()
        input_sha256 = inputs[doc.id][1]

        def mutate(
            current: Article,
            vector: list[float] = vector,
            content: str = content,
            input_sha256: str = input_sha256,
        ) -> Article | None:
            if current.compute_sha256() != content:
                return None
            return current.model_copy(
                update={
                    "embedding": [float(v) for v in vector],
                    "embedding_sha256": input_sha256,
                }
            )

        try:
            await update_doc(store, Article, doc.id, mutate)
            written += 1
        except StoreError as exc:
            _logger.warning(
                "gates_backfill_failed", extra={"article_id": doc.id, **describe_exception(exc)}
            )
    return written
