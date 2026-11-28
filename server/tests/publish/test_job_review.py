"""Calibration on approval (``classify_review``) against the **pinned gated version**: the
pipeline's draft as gated, not whatever the article says now."""

from __future__ import annotations

import pytest

from blogwriter.core.testing import asgi_client
from blogwriter.domain.articles import Article, CalibrationFlags, GateVerdict
from blogwriter.domain.base import to_iso
from blogwriter.domain.docstore import read_doc, read_singleton
from blogwriter.domain.settings import AutonomySettings
from blogwriter.domain.testing import FakeServices, make_services
from blogwriter.publish.review import classify_review, gated_body, review_outcome
from blogwriter.publish.testing import (
    article_body,
    make_harness,
    owner_headers,
    publish_app,
    seed_article,
)

BODY = article_body()
REWRITTEN = article_body(extra=" " + " ".join(f"word{index}" for index in range(30)))


@pytest.fixture
def services() -> FakeServices:
    return make_services()


async def test_a_reviewed_draft_approved_as_gated_is_clean(services: FakeServices) -> None:
    article = await seed_article(services)
    assert classify_review(article, BODY, article.markdown) == "clean"
    assert await review_outcome(services.store, article) == ("clean", False)


async def test_the_gated_body_comes_from_the_pinned_version(services: FakeServices) -> None:
    # The article now holds the rewritten body; the pinned gated version holds the original.
    article = await seed_article(services, body=REWRITTEN, gated_body=BODY)
    assert article.calibration is not None
    assert article.calibration.gated_version == 1
    assert await gated_body(services.store, article) == BODY
    assert await review_outcome(services.store, article) == ("edited", True)


async def test_small_edits_stay_clean() -> None:
    services = make_services()
    small = BODY.replace("deploy.", "release.")
    article = await seed_article(services, body=small, gated_body=BODY)
    assert classify_review(article, BODY, small) == "clean"


async def test_without_a_gated_version_the_review_counts_as_edited(services: FakeServices) -> None:
    article = await seed_article(services)
    owner_written = article.model_copy(update={"calibration": None})
    assert await gated_body(services.store, owner_written) == ""
    assert classify_review(owner_written, "", owner_written.markdown) == "edited"
    missing = article.model_copy(update={"calibration": CalibrationFlags(gated_version=9)})
    assert await gated_body(services.store, missing) == ""  # unreadable: fail closed


async def test_review_only_and_failed_gates_come_first(services: FakeServices) -> None:
    article = await seed_article(services)
    review_only = article.model_copy(update={"review_only": True})
    assert classify_review(review_only, BODY, BODY) == "review_only"
    assert article.gates is not None
    overridden = article.gates.model_copy(
        update={
            "results": [
                result.model_copy(
                    update={
                        "verdict": GateVerdict.FAIL if index == 3 else result.verdict,
                    }
                )
                for index, result in enumerate(article.gates.results)
            ]
        }
    )
    failed = article.model_copy(update={"gates": overridden})
    assert classify_review(failed, BODY, BODY) == "gates_failed"
    pipeline_failed = article.model_copy(
        update={"calibration": CalibrationFlags(gated_version=1, gates_passed=False)}
    )
    assert classify_review(pipeline_failed, BODY, BODY) == "gates_failed"
    stale = article.model_copy(
        update={"gates": article.gates.model_copy(update={"content_sha256": "0" * 64})}
    )
    assert classify_review(stale, BODY, BODY) == "gates_failed"


async def test_approval_records_the_outcome_and_resets_the_streak_on_edits() -> None:
    harness = make_harness()
    store = harness.services.store
    clean = await seed_article(harness.services)
    edited = await seed_article(harness.services, body=REWRITTEN, gated_body=BODY)
    async with asgi_client(publish_app(harness)) as client:
        for article in (clean, edited):
            etag = (await read_doc(store, Article, article.id)).etag
            response = await client.post(
                f"/api/articles/{article.id}/approve", json={}, headers=owner_headers(etag)
            )
            assert response.status_code == 200, response.text
    now = to_iso(harness.services.clock.now())
    autonomy, _ = await read_singleton(store, AutonomySettings, now=now)
    assert [event.outcome for event in autonomy.calibration.events] == ["clean", "edited"]
    assert autonomy.calibration.clean_streak == 0
    flags = (await read_doc(store, Article, edited.id)).doc.calibration
    assert flags is not None
    assert flags.outcome == "edited"
    assert flags.substantive_edit is True
    assert flags.counted_at == now
