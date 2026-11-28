"""``lint_post_text``: the display text and every published text (title, slides, poll) go
through the same lint; blocklist, standards ids, deprecated names, slop, links, hashtags,
carousel rules. Lint blocks approval."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest

from blogwriter.domain.common import Finding
from blogwriter.domain.posts import HookType, HookVariant, LinkException, Post
from blogwriter.domain.settings import (
    Blocklist,
    BlocklistTerm,
    LaneSettings,
    PillarSettings,
    StandardsCatalog,
)
from blogwriter.domain.testing import SAMPLE_NOW, FakeSlopChecker, sample_document
from blogwriter.linkedin.posts._post import finding_field
from blogwriter.linkedin.posts.carousel import spec_json
from blogwriter.linkedin.posts.checks import lint_post_text
from blogwriter.linkedin.posts.serialize import serialize
from blogwriter.linkedin.posts.testing import CLEAN_BODY, carousel_slides
from blogwriter.render.spec import CarouselSpec, CoverSlide, ListSlide, StatementSlide

NOW = datetime(2026, 11, 26, 12, tzinfo=UTC)
GOLDENS = Path(__file__).parent / "goldens"
HASHTAGS = ("AISecurity",)
BLOCKLIST = Blocklist(
    id=Blocklist.SINGLETON_ID,
    created_at=SAMPLE_NOW,
    updated_at=SAMPLE_NOW,
    configured=True,
    terms=[BlocklistTerm(id="t-1", term="Fabrikam", category="client")],
)
CATALOG = StandardsCatalog.default(SAMPLE_NOW)
LANES = LaneSettings.default(SAMPLE_NOW)


def pillars() -> PillarSettings:
    defaults = PillarSettings.default(SAMPLE_NOW)
    return defaults.model_copy(
        update={
            "pillars": [
                p.model_copy(update={"hashtags": list(HASHTAGS)}) if p.id == "agents" else p
                for p in defaults.pillars
            ]
        }
    )


def make_post(
    text: str = CLEAN_BODY, /, *, hashtags: Sequence[str] = HASHTAGS, **fields: object
) -> Post:
    tags = list(hashtags)
    display, payload = serialize(text, tags)
    return sample_document(
        Post, display_text=display, payload=payload, hashtags=tags, pillar="agents", **fields
    )


def lint(
    post: Post,
    *,
    blocklist: Blocklist = BLOCKLIST,
    slop: FakeSlopChecker | None = None,
) -> list[Finding]:
    return lint_post_text(
        post,
        hashtags=post.hashtags,
        blocklist=blocklist,
        slop=slop if slop is not None else FakeSlopChecker(),
        catalog=CATALOG,
        pillars=pillars(),
        lanes=LANES,
        now=NOW,
    )


def blocking(findings: Sequence[Finding]) -> list[tuple[str, str | None]]:
    return [(f.code, finding_field(f)) for f in findings if f.severity == "block"]


def codes(findings: Sequence[Finding], field: str | None = None) -> set[str]:
    return {f.code for f in findings if field is None or finding_field(f) == field}


def carousel_post(slides: Sequence[object], *, title: str = "Sequencing a 2,000-app move") -> Post:
    spec = CarouselSpec.model_validate({"title": title, "slides": list(slides)})
    return make_post(
        "We moved 2,000 apps in 14 months. Swipe for the order that worked.",
        format="carousel",
        carousel_spec=spec_json(spec),
        document_title=spec.title,
    )


def test_a_clean_text_post_has_no_blocking_findings() -> None:
    assert blocking(lint(make_post())) == []


# The same phrase in the display text and on a slide gives the same finding code.
SAME_LINT = [
    ("Agree?", "engagement-bait"),
    ("We leverage tokens per call.", "ai-tell"),
    ("See example.com for the list.", "domain-like"),
    ("The full list: https://example.org/apps", "external-link"),
    ("We moved \U0001d42d\U0001d421\U0001d41e apps.", "unicode-styled-text"),
    ("Fabrikam ran the pilot.", "blocklisted-term"),
    ("LLM99 is the risk.", "standard-id-unknown"),
    ("We left Azure AD behind.", "deprecated-name"),
]


@pytest.mark.parametrize(("phrase", "code"), SAME_LINT)
def test_slide_text_goes_through_the_same_lint_as_the_display_text(phrase: str, code: str) -> None:
    in_text = lint(make_post(f"{CLEAN_BODY}\n\n{phrase}"))
    assert code in codes(in_text, "displayText")
    slides = carousel_slides(8)
    slides[1] = StatementSlide(statement=phrase)
    on_slide = lint(carousel_post(slides))
    assert code in codes(on_slide, "slides[1]")
    severity_text = {f.severity for f in in_text if f.code == code}
    severity_slide = {f.severity for f in on_slide if f.code == code}
    assert severity_text == severity_slide


def test_formula_openers_block_on_the_cover_too() -> None:
    slides = carousel_slides(8)
    slides[0] = CoverSlide(hook="Unpopular opinion: count the strange apps")
    found = lint(carousel_post(slides))
    assert ("formula-opener", "slides[0]") in blocking(found)


def test_an_unconfigured_blocklist_is_one_hard_block() -> None:
    unconfigured = BLOCKLIST.model_copy(update={"configured": False, "terms": []})
    found = lint(make_post(), blocklist=unconfigured)
    hard = [f for f in found if f.code == "blocklist-not-configured"]
    assert len(hard) == 1
    assert hard[0].severity == "block"
    assert not hard[0].overridable


def test_blocklist_findings_never_echo_the_term() -> None:
    found = lint(make_post(f"{CLEAN_BODY}\n\nFabrikam ran the pilot."))
    hits = [f for f in found if f.code == "blocklisted-term"]
    assert hits
    assert all("Fabrikam" not in f.message for f in hits)
    assert all(f.ref == "t-1" for f in hits)


def test_links_block_without_an_exception() -> None:
    found = lint(make_post(f"{CLEAN_BODY}\n\nhttps://example.org/a"))
    assert ("external-link", "displayText") in blocking(found)


def test_the_link_exception_allows_only_its_url_on_its_own_line() -> None:
    url = "https://example.org/report"
    exception = LinkException(url=url, reason="Link experiment arm B", approved_at=SAMPLE_NOW)
    ok = lint(make_post(f"{CLEAN_BODY}\n\n{url}", link_exception=exception))
    assert blocking(ok) == []
    assert "link-exception" in codes(ok)
    inline = lint(make_post(f"{CLEAN_BODY} See {url} now.", link_exception=exception))
    assert ("link-own-line", "displayText") in blocking(inline)
    other = lint(make_post(f"{CLEAN_BODY}\n\nhttps://example.org/other", link_exception=exception))
    assert ("link-not-excepted", "displayText") in blocking(other)


def test_hashtags_must_be_on_the_pillar_list() -> None:
    found = lint(make_post(hashtags=["AISecurity", "Random"]))
    assert ("hashtag-not-approved", "hashtags") in blocking(found)


def test_the_carousel_rules_block() -> None:
    short = lint(carousel_post(carousel_slides(8)[:3]))
    assert "carousel-slide-count" in codes(short)
    long_title = lint(carousel_post(carousel_slides(8), title="T" * 70))
    assert "carousel-title-length" in codes(long_title)
    no_recap = carousel_slides(8)
    no_recap[-1] = StatementSlide(statement="No recap at the end.")
    assert "carousel-last-slide" in codes(lint(carousel_post(no_recap)))


def test_a_carousel_needs_its_title_in_sync_and_a_short_caption() -> None:
    post = carousel_post(carousel_slides(8))
    drifted = post.model_copy(update={"document_title": "Another title"})
    assert ("document-title-mismatch", "documentTitle") in blocking(lint(drifted))
    display, payload = serialize("x " * 400, list(HASHTAGS))
    long_caption = post.model_copy(update={"display_text": display, "payload": payload})
    assert ("caption-too-long", "displayText") in blocking(lint(long_caption))


def test_an_image_card_has_one_slide() -> None:
    spec = CarouselSpec(
        title="Order of a migration",
        slides=[ListSlide(title="Order", items=["Odd apps first", "Easy apps last"])],
    )
    card = make_post(format="image_card", carousel_spec=spec_json(spec))
    assert blocking(lint(card)) == []
    two = spec.model_copy(update={"slides": [*spec.slides, *spec.slides]})
    assert "card-slides" in codes(lint(card.model_copy(update={"carousel_spec": spec_json(two)})))


def test_slop_findings_are_shown_and_a_missing_checker_blocks() -> None:
    slop = FakeSlopChecker(
        [Finding(code="colon-reveal", severity="warn", message="Colon reveal.", start=0, end=3)]
    )
    found = lint(make_post(), slop=slop)
    assert "colon-reveal" in codes(found, "displayText")
    missing = lint_post_text(
        make_post(),
        hashtags=HASHTAGS,
        blocklist=BLOCKLIST,
        slop=None,
        catalog=CATALOG,
        pillars=pillars(),
        now=NOW,
    )
    assert ("slop-unavailable", None) in blocking(missing)


def test_a_payload_that_drifted_from_the_text_blocks() -> None:
    post = make_post()
    assert ("payload-mismatch", "payload") in blocking(
        lint(post.model_copy(update={"payload": "x"}))
    )


def test_hooks_without_owner_wording_and_question_hooks_warn() -> None:
    variants = [
        HookVariant(
            id="h1",
            text="Why do migrations slip?",
            hook_type=HookType.QUESTION,
            concrete_detail="slip",
        ),
        HookVariant(
            id="h2",
            text="Fabrikam slipped",
            hook_type=HookType.MISTAKE_LESSON,
            concrete_detail="slip",
        ),
    ]
    post = make_post(hook_variants=variants, selected_hook_id="h1", hook_type=HookType.QUESTION)
    found = lint(post)
    assert {"hooks-no-owner-wording", "question-hook"} <= codes(found)
    assert ("blocklisted-term", "hookVariants[1]") in blocking(found)


def test_polls_are_linted() -> None:
    missing = make_post(format="poll")
    assert ("poll-missing", "poll") in blocking(lint(missing))
    bad = make_post(format="poll", poll={"question": "Q?", "options": ["only one"]})
    assert ("poll-invalid", "poll") in blocking(lint(bad))
    baity = make_post(
        format="poll", poll={"question": "Agree?", "options": ["Yes", "No"], "duration": "ONE_DAY"}
    )
    assert ("engagement-bait", "poll.question") in blocking(lint(baity))


def test_lint_golden() -> None:
    text = (
        "Unpopular opinion: we leverage 3 tools.\n\n"
        "See README.md and https://example.org/x. Fabrikam agreed.\n\n"
        "Agree?\n\n#AISecurity #Other"
    )
    found = lint(make_post(text))
    produced = [
        {
            "code": f.code,
            "severity": f.severity,
            "field": finding_field(f),
            "start": f.start,
            "end": f.end,
        }
        for f in found
    ]
    golden = json.loads((GOLDENS / "lint.json").read_text(encoding="utf-8"))
    assert produced == golden


def test_more_than_two_emoji_block_approval() -> None:
    """The plan's post lint blocks approval on more than 2 emoji (the Phase 0 lint warns)."""
    findings = lint(make_post(CLEAN_BODY + "\n\nShipped 🚀🔥✅"))
    assert ("too-many-emoji", "displayText") in blocking(findings)
    assert "too-many-emoji" not in codes(lint(make_post(CLEAN_BODY + "\n\nShipped 🚀🔥")))
