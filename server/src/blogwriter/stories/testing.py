"""Helpers for tests that use the stories package (no network, no Azure, no real model).

Example::

    services = make_services()
    await save_policies(services.store)                     # blocklist saved, AI use confirmed
    services.ai.add(Task.FACT_CHECK, classifier_reply("pass", "fail"))
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final, Literal

from blogwriter.db import Store
from blogwriter.domain.docstore import read_singleton, save_singleton
from blogwriter.domain.settings import Blocklist, BlocklistTerm, EmployerPolicy
from blogwriter.domain.testing import SAMPLE_NOW
from blogwriter.stories.classify import ClassifierFinding, ClassifierReply, ClassifierVerdict

__all__ = [
    "SAMPLE_TERMS",
    "classifier_reply",
    "flagged",
    "save_policies",
]

SAMPLE_TERMS: Final = (
    BlocklistTerm(id="t-1", term="Contoso", category="client"),
    BlocklistTerm(id="t-2", term="Falcon", category="project"),
    BlocklistTerm(id="t-3", term="Northwind", category="client", match="substring"),
)
"""Fictional confidential names (a client, an internal project, a substring match)."""


async def save_policies(
    store: Store,
    *,
    terms: Sequence[BlocklistTerm] = SAMPLE_TERMS,
    blocklist: bool = True,
    ai_use: bool = True,
    now: str = SAMPLE_NOW,
) -> None:
    """Save the blocklist (``configured`` when ``blocklist``) and the employer policy."""
    current, etag = await read_singleton(store, Blocklist, now=now)
    saved = current.model_copy(update={"terms": list(terms), "configured": blocklist})
    if blocklist:
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


def flagged(
    category: Literal[
        "client_identity", "person", "internal_system", "non_public_figure", "mosaic", "other"
    ],
    excerpt: str,
    *,
    severity: Literal["block", "warn"] = "block",
) -> ClassifierFinding:
    """One model finding for :func:`classifier_reply`."""
    return ClassifierFinding(category=category, severity=severity, excerpt=excerpt)


def classifier_reply(
    *verdicts: Literal["pass", "fail"] | Sequence[ClassifierFinding],
    ids: Sequence[str] | None = None,
) -> ClassifierReply:
    """A classifier reply for texts ``t1``, ``t2``... (or ``ids``): ``"pass"``, ``"fail"``, or
    a list of findings (``fail`` when any of them blocks)."""
    results: list[ClassifierVerdict] = []
    keys = list(ids) if ids is not None else [f"t{n}" for n in range(1, len(verdicts) + 1)]
    for key, verdict in zip(keys, verdicts, strict=True):
        if isinstance(verdict, str):
            results.append(ClassifierVerdict(id=key, verdict=verdict, findings=[]))
            continue
        findings = list(verdict)
        failed = any(finding.severity == "block" for finding in findings)
        results.append(
            ClassifierVerdict(id=key, verdict="fail" if failed else "pass", findings=findings)
        )
    return ClassifierReply(results=results)
