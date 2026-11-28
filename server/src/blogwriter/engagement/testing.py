"""Helpers for tests that use the engagement package (no network, no Azure, no real model).

Example::

    services = make_services()
    await save_policies(services.store)          # blocklist saved, AI use confirmed
    await create_doc(services.store, usable_story(title="Okta to Entra ID"))
    services.ai.add(Task.ENGAGEMENT, angles_reply(("We moved SAML apps last...", ["s1"])))
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

from blogwriter.db import Store
from blogwriter.domain.base import sha256_hex
from blogwriter.domain.common import ClassifierCheck
from blogwriter.domain.docstore import read_singleton, save_singleton
from blogwriter.domain.settings import Blocklist, BlocklistTerm, EmployerPolicy
from blogwriter.domain.states import ReviewStatus
from blogwriter.domain.stories import PovEntry, StoryEntry
from blogwriter.domain.testing import SAMPLE_NOW, sample_document
from blogwriter.engagement.models import AngleDraft, AnglesReply
from blogwriter.stories import entry_text

__all__ = ["TEST_TERMS", "angles_reply", "save_policies", "usable_pov", "usable_story"]

TEST_TERMS: Final = (
    BlocklistTerm(id="t-1", term="Contoso", category="client"),
    BlocklistTerm(id="t-2", term="Falcon", category="project"),
)
"""Fictional confidential names (a client and an internal project)."""


async def save_policies(
    store: Store,
    *,
    terms: Sequence[BlocklistTerm] = TEST_TERMS,
    blocklist: bool = True,
    ai_use: bool = True,
    now: str = SAMPLE_NOW,
) -> None:
    """Save the blocklist (only when ``blocklist``) and the employer policy (AI use confirmed
    when ``ai_use``)."""
    if blocklist:
        current, etag = await read_singleton(store, Blocklist, now=now)
        saved = current.model_copy(update={"terms": list(terms), "configured": True})
        await save_singleton(store, saved, if_match=etag)
    policy, policy_etag = await read_singleton(store, EmployerPolicy, now=now)
    changed = policy.model_copy(
        update={
            "ai_use_confirmed": ai_use,
            "ai_use_confirmed_at": now if ai_use else None,
            "configured": True,
        }
    )
    await save_singleton(store, changed, if_match=policy_etag)


def _passed(text: str) -> ClassifierCheck:
    return ClassifierCheck(passed=True, checked_at=SAMPLE_NOW, content_sha256=sha256_hex(text))


def usable_story(**fields: object) -> StoryEntry:
    """An approved story whose classifier verdict passed its current text (``usable``)."""
    story = sample_document(
        StoryEntry, status=ReviewStatus.APPROVED, approved_at=SAMPLE_NOW, **fields
    )
    return story.model_copy(update={"classifier": _passed(entry_text(story))})


def usable_pov(**fields: object) -> PovEntry:
    """An approved POV entry whose classifier verdict passed its current text (``usable``)."""
    pov = sample_document(PovEntry, status=ReviewStatus.APPROVED, approved_at=SAMPLE_NOW, **fields)
    return pov.model_copy(update={"classifier": _passed(entry_text(pov))})


def angles_reply(*angles: tuple[str, Sequence[str]]) -> AnglesReply:
    """A scripted ENGAGEMENT reply: ``(text, refs)`` per angle."""
    return AnglesReply(
        angles=[AngleDraft(text=text, grounded_in=list(refs)) for text, refs in angles]
    )
