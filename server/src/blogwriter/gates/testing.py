"""Test helpers for code that runs the gates (gates, pipeline and analytics tests).

:func:`gate_world` builds a :class:`~blogwriter.domain.testing.FakeServices` with a clean,
publishable article that passes all seven gates once :func:`script_passing_ai` has queued the
model replies: a configured blocklist and employer policy (AI use confirmed), a brief with one
allowlisted citation, an approved and classified story, the citation's page for the link check
and the fact check. No network, no Azure.
"""

from __future__ import annotations

from dataclasses import dataclass

from blogwriter.ai.types import Task
from blogwriter.domain.articles import Article, ArticleMeta, OwnerSignalRef
from blogwriter.domain.common import Citation, ClassifierCheck, Quote
from blogwriter.domain.docstore import create_doc, save_singleton
from blogwriter.domain.research import Brief
from blogwriter.domain.settings import Blocklist, BlocklistTerm, EmployerPolicy
from blogwriter.domain.stories import StoryEntry
from blogwriter.domain.testing import (
    SAMPLE_NOW,
    FakeServices,
    ScriptedModelRunner,
    StaticPageFetcher,
    make_services,
    sample_document,
)
from blogwriter.gates.factcheck import ClaimMap, ClaimVerdict, MappedClaim, SourceCheck
from blogwriter.gates.rubric import RubricScores

__all__ = [
    "BLOCKED_TERM",
    "SAMPLE_BODY",
    "SAMPLE_CITATION",
    "SAMPLE_META",
    "SAMPLE_PASSAGE",
    "SAMPLE_URL",
    "GateWorld",
    "gate_world",
    "passing_scores",
    "sample_brief",
    "saved_blocklist",
    "script_passing_ai",
    "seed_policy",
    "stored_article",
    "usable_story",
]

SAMPLE_URL = "https://learn.microsoft.com/entra/identity/okta-migration"
SAMPLE_CITATION = "src-entra"
BLOCKED_TERM = "Contoso"
SAMPLE_PASSAGE = (
    "Guidance for moving applications from Okta to Microsoft Entra ID. Plan the attribute "
    "mapping before you migrate applications, and test provisioning on a pilot group."
)
SAMPLE_META = ArticleMeta(
    title="Moving 2,000 apps from Okta to Entra",
    slug="okta-to-entra",
    excerpt="What broke first in a 14-month identity migration, and what I would change.",
    pillar="identity",
    format="deep-explainer",
    tags=["identity", "migration"],
)
SAMPLE_BODY = f"""Last March I moved 2,000 apps from Okta to Microsoft Entra ID over 14 months. \
SCIM provisioning broke for 37 of them because the attribute mapping used \
`extensionAttribute3`, which our HR feed never populated.[^{SAMPLE_CITATION}]

## What broke first

We fixed it with a two-week freeze and a script that diffed the Okta profile against the Entra \
user object. The script found 412 mismatches, and most were stale department codes from a 2019 \
reorg that nobody had cleaned up.

## What I would change

Budget a week for attribute archaeology before you touch a single app. I learned that the \
expensive way, after 3 rollbacks in week 6.

[^{SAMPLE_CITATION}]: Microsoft Learn. *Plan an Okta to Entra migration*. \
[learn.microsoft.com]({SAMPLE_URL})
"""


def usable_story(**overrides: object) -> StoryEntry:
    """An approved story the classifier passed (owner signal for gate 6)."""
    fields: dict[str, object] = {
        "status": "approved",
        "confidentiality": "public",
        "classifier": ClassifierCheck(passed=True, checked_at=SAMPLE_NOW),
        "approved_at": SAMPLE_NOW,
    }
    fields.update(overrides)
    return sample_document(StoryEntry, **fields)


def sample_brief(**overrides: object) -> Brief:
    """A brief citing :data:`SAMPLE_URL` (allowlisted) with one short quote."""
    fields: dict[str, object] = {
        "pillar": "identity",
        "topic": "Okta to Entra migrations",
        "citations": [
            Citation(
                id=SAMPLE_CITATION,
                url=SAMPLE_URL,
                title="Plan an Okta to Entra migration",
                publisher="Microsoft Learn",
                domain="learn.microsoft.com",
                allowlisted=True,
            )
        ],
        "quotes": [
            Quote(
                id="q1",
                citation_id=SAMPLE_CITATION,
                text="Plan the attribute mapping before you migrate applications.",
            )
        ],
    }
    fields.update(overrides)
    return sample_document(Brief, **fields)


def stored_article(**overrides: object) -> Article:
    """The sample article (``content_sha256`` set from its meta and body)."""
    fields: dict[str, object] = {
        "status": "in_review",
        "meta": SAMPLE_META,
        "markdown": SAMPLE_BODY,
        "version": 3,
    }
    fields.update(overrides)
    article = sample_document(Article, **fields)
    return article.model_copy(update={"content_sha256": article.compute_sha256()})


def passing_scores(**overrides: int) -> RubricScores:
    values: dict[str, int] = {
        "clarity": 9,
        "structure": 9,
        "accuracy": 9,
        "depth": 8,
        "first_hand_specificity": 9,
        "voice": 8,
        "originality": 8,
        "actionability": 8,
        "overall": 9,
    }
    values.update(overrides)
    return RubricScores.model_validate(values)


def script_passing_ai(runner: ScriptedModelRunner, *, story_id: str) -> None:
    """Queue FACT_CHECK (map, verify) and CRITIQUE replies under which the sample passes."""
    runner.add(
        Task.FACT_CHECK,
        ClaimMap(
            claims=[
                MappedClaim(
                    id="c1",
                    quote="SCIM provisioning broke for 37 of them",
                    citations=[SAMPLE_CITATION],
                    support="owner_material",
                    support_ref=story_id,
                )
            ]
        ),
        SourceCheck(verdicts=[ClaimVerdict(id="c1", verdict="supported")]),
    )
    runner.add(Task.CRITIQUE, passing_scores())


def saved_blocklist(*terms: str) -> Blocklist:
    """A blocklist the owner saved: ``terms`` (default :data:`BLOCKED_TERM`) as client names,
    ids ``client-1``..."""
    chosen = terms or (BLOCKED_TERM,)
    return Blocklist(
        id=Blocklist.SINGLETON_ID,
        created_at=SAMPLE_NOW,
        updated_at=SAMPLE_NOW,
        configured=True,
        terms=[
            BlocklistTerm(id=f"client-{index + 1}", term=term, category="client")
            for index, term in enumerate(chosen)
        ],
    )


async def seed_policy(
    services: FakeServices,
    *,
    blocklist: bool = True,
    ai_use_confirmed: bool = True,
    policy_configured: bool = True,
    forbidden_topics: list[str] | None = None,
) -> None:
    """Save the blocklist (one client term) and the employer policy."""
    if blocklist:
        await save_singleton(services.store, saved_blocklist(), if_match=None)
    await save_singleton(
        services.store,
        EmployerPolicy(
            id=EmployerPolicy.SINGLETON_ID,
            created_at=SAMPLE_NOW,
            updated_at=SAMPLE_NOW,
            configured=policy_configured,
            ai_use_confirmed=ai_use_confirmed,
            ai_use_confirmed_at=SAMPLE_NOW if ai_use_confirmed else None,
            forbidden_topics=forbidden_topics or [],
        ),
        if_match=None,
    )


@dataclass
class GateWorld:
    services: FakeServices
    article: Article
    brief: Brief
    story: StoryEntry


async def gate_world(
    *,
    services: FakeServices | None = None,
    blocklist: bool = True,
    ai_use_confirmed: bool = True,
    article: Article | None = None,
    store_article: bool = True,
) -> GateWorld:
    """Services with a clean article that passes every gate (script the AI first)."""
    world = services if services is not None else make_services()
    await seed_policy(world, blocklist=blocklist, ai_use_confirmed=ai_use_confirmed)
    story = usable_story()
    await create_doc(world.store, story)
    brief = sample_brief()
    await create_doc(world.store, brief)
    chosen = article or stored_article(
        brief_id=brief.id, owner_signals=[OwnerSignalRef(kind="story", id=story.id)]
    )
    if store_article:
        await create_doc(world.store, chosen)
    world.egress.add(SAMPLE_URL, b"<!doctype html><title>Plan</title>")
    world.fetcher = StaticPageFetcher({SAMPLE_URL: SAMPLE_PASSAGE})
    return GateWorld(services=world, article=chosen, brief=brief, story=story)
