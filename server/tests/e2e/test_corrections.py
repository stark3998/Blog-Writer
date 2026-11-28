"""After the first publish, as the owner: a correction of the live article (back to review, an
edit and a new slug, the clearance and the gates again, publish now with a dated note, a
redirect stub at the old slug, auto-publish turned off), then an unpublish (a tombstone, the
page gone, verified)."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta

from pydantic import JsonValue

from blogwriter.domain.articles import PUBLISHED_MARKER_NAME, Article
from blogwriter.domain.docstore import read_doc
from blogwriter.domain.states import ArticleStatus
from blogwriter.publish.contract import validate_published_doc
from blogwriter.publish.testing import SITE

from .conftest import (
    System,
    dig,
    draft_flagship,
    prepare_owner,
    review_and_publish,
    script_gates_again,
    start_system,
)

S = ArticleStatus
NOTE = "Tokens are short-lived as well as scoped to one call."
NEW_SLUG = "scope-agent-tokens-to-the-call"


async def article(system: System, article_id: str) -> Article:
    return (await read_doc(system.store, Article, article_id)).doc


async def test_a_correction_with_a_new_slug_then_an_unpublish() -> None:
    async with start_system() as system:
        owner = await prepare_owner(system)
        flagship = await draft_flagship(system, owner)
        published = await review_and_publish(system, flagship)
        path = f"/api/articles/{published.article_id}"
        first = (await system.store.read("published-blogs", published.slug)).body
        await system.run_for(timedelta(days=1))

        # Back to review: the live version stays up while the owner corrects it.
        item, etag = await system.item(path)
        moved = await system.ok("POST", f"{path}/transition", {"to": "in_review"}, etag=etag)
        assert dig(moved, "item", "status") == "in_review"
        assert dig(moved, "item", "publish", "live") is True
        assert system.portfolio.pages[published.slug][0] == 200
        markdown = dig(item, "markdown")
        assert isinstance(markdown, str)
        corrected = markdown.replace(
            "Issue a token per tool call", "Issue a short-lived token per tool call"
        )
        meta = dig(item, "meta")
        assert isinstance(meta, dict)
        saved = await system.ok(
            "PUT",
            path,
            {
                "meta": {**meta, "slug": NEW_SLUG},
                "markdown": corrected,
                "baseVersion": dig(item, "version"),
            },
            etag=_etag(moved),
        )
        assert dig(saved, "item", "version") == 3
        await system.ok("POST", f"{path}/clearance", {}, etag=_etag(saved))
        script_gates_again(system)
        rerun = await system.ok("POST", f"{path}/gates/run", status=202)
        await system.wait_job(str(rerun["jobId"]))
        await system.idle()

        # Publish now: a live article needs the dated note, a new slug needs confirming.
        _, etag = await system.item(path)
        refused = await system.send("POST", f"{path}/publish-now", {"slug": NEW_SLUG}, etag=etag)
        assert refused.status_code == 409
        assert refused.json()["code"] == "correction_note_required"
        unconfirmed = await system.send(
            "POST", f"{path}/publish-now", {"correctionNote": NOTE}, etag=etag
        )
        assert unconfirmed.status_code == 409
        assert unconfirmed.json()["code"] == "slug_change_unconfirmed"
        now = await system.ok(
            "POST", f"{path}/publish-now", {"correctionNote": NOTE, "slug": NEW_SLUG}, etag=etag
        )
        job_id = str(now["jobId"])
        await system.wait_job(job_id)
        await system.idle()
        corrected_doc = (await system.store.read("published-blogs", NEW_SLUG)).body
        assert validate_published_doc(corrected_doc, now=system.clock.now()) == []
        day = system.clock.now().strftime("%Y-%m-%d")
        assert corrected_doc["corrections"] == [{"date": day, "note": NOTE}]
        assert corrected_doc["publishedAt"] == first["publishedAt"]  # first published date kept
        stub = (await system.store.read("published-blogs", published.slug)).body
        assert (stub["status"], stub["redirectTo"]) == ("redirect", NEW_SLUG)
        status, page = system.portfolio.pages[NEW_SLUG]
        assert status == 200
        assert f'name="{PUBLISHED_MARKER_NAME}" content="{corrected_doc["updatedAt"]}"' in page
        assert system.portfolio.pages[published.slug][0] == 200  # the old address redirects
        live = await article(system, published.article_id)
        assert live.status is S.PUBLISHED
        assert live.publish.slug == NEW_SLUG
        assert live.publish.previous_slugs == [published.slug]
        assert live.publish.url == f"{SITE}/blog/{NEW_SLUG}"
        assert live.publish.last_published_version == 3
        assert live.calibration is not None
        assert live.calibration.outcome == "correction"
        autonomy = await system.ok("GET", "/api/kb/autonomy")
        assert dig(autonomy, "settings", "item", "autoPublishEnabled") is False
        assert dig(autonomy, "settings", "item", "calibration", "cleanStreak") == 0
        assert dig(autonomy, "settings", "item", "lastCorrectionAt") is not None
        assert system.portfolio.deploy_dispatches() == 2

        # Unpublish: a tombstone that keeps the slug, a build, the page verified gone.
        await system.run_for(timedelta(days=1))
        _, etag = await system.item(path)
        accepted = await system.ok("POST", f"{path}/unpublish", etag=etag, status=202)
        await system.wait_job(str(accepted["jobId"]))
        await system.idle()
        tombstone = (await system.store.read("published-blogs", NEW_SLUG)).body
        assert tombstone["status"] == "unpublished"
        assert "htmlContent" not in tombstone
        assert validate_published_doc(tombstone, now=system.clock.now()) == []
        assert NEW_SLUG not in system.portfolio.pages
        assert published.slug not in system.portfolio.pages  # the stub points at nothing live
        gone = await article(system, published.article_id)
        assert gone.status is S.UNPUBLISHED
        assert gone.publish.live is False
        assert gone.publish.last_operation == "unpublish"
        assert system.portfolio.deploy_dispatches() == 3


def _etag(body: Mapping[str, JsonValue]) -> str:
    etag = body["etag"]
    assert isinstance(etag, str)
    return etag
