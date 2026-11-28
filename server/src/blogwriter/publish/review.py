"""Auto-publish calibration on approval (plan: "Auto-publish calibration").

Auto-publish switches on after ``Calibration.required`` (6) consecutive reviewed articles that
pass the gates and are approved without substantive edits. On every approval the publish
package classifies the review (:func:`classify_review`) against the **pinned gated version**
(``article.calibration.gatedVersion``: the pipeline's draft as gated, kept by version pruning)
and records it in ``AutonomySettings.calibration`` (``record_review``); a correction of a live
article records ``record_correction`` instead, which also turns auto-publish off.

Order of the outcomes (the first that applies):

1. ``review_only``: the draft was produced by a fallback model or with a refusal in the chain.
2. ``gates_failed``: the current gate report did not pass on its own (overrides never count),
   or the pipeline's gated version did not pass.
3. ``edited``: the owner's edits changed more than 5% of the words of the gated body (also when
   there is no gated version at all: the owner wrote it, so it says nothing about the pipeline).
4. ``clean``.

While the embedder is not served (contract I-3) gate 4 never passes on its own, so no review is
``clean`` and auto-publish cannot switch on before embeddings exist.
"""

from __future__ import annotations

import asyncio

from blogwriter.db import NotFound, Store
from blogwriter.domain.articles import Article, ArticleVersion
from blogwriter.domain.docstore import InvalidStoredDocument, read_doc
from blogwriter.domain.settings import CalibrationOutcome, is_substantive_edit

__all__ = ["classify_review", "gated_body", "review_outcome"]


def classify_review(article: Article, gated_body: str, approved_body: str) -> CalibrationOutcome:
    """The calibration outcome of approving ``article`` (sync and CPU-bound for long bodies:
    :func:`review_outcome` runs it in a thread).

    ``gated_body`` is the body of the ``ArticleVersion`` at ``article.calibration.gatedVersion``
    (``""`` when there is none) and ``approved_body`` the body being approved.
    """
    flags = article.calibration
    if article.review_only or (flags is not None and flags.review_only):
        return "review_only"
    report = article.gates
    report_passed = (
        report is not None
        and report.content_sha256 == article.compute_sha256()
        and report.auto_publish_ready
    )
    if not report_passed or (flags is not None and flags.gates_passed is False):
        return "gates_failed"
    if is_substantive_edit(gated_body, approved_body):
        return "edited"
    return "clean"


async def gated_body(store: Store, article: Article) -> str:
    """The body of the pinned gated version, or ``""`` when there is none (or it cannot be
    read: fail closed, the review then counts as ``edited``)."""
    flags = article.calibration
    if flags is None or flags.gated_version is None:
        return ""
    try:
        stored = await read_doc(
            store,
            ArticleVersion,
            ArticleVersion.id_for(article.id, flags.gated_version),
            partition_key=article.id,
        )
    except (NotFound, InvalidStoredDocument):
        return ""
    return stored.doc.markdown


async def review_outcome(store: Store, article: Article) -> tuple[CalibrationOutcome, bool]:
    """``(outcome, substantive_edit)`` of approving ``article`` as it is now."""
    before = await gated_body(store, article)
    outcome = await asyncio.to_thread(classify_review, article, before, article.markdown)
    substantive = await asyncio.to_thread(is_substantive_edit, before, article.markdown)
    return outcome, substantive
