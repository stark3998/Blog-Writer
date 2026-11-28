"""The confidentiality classifier and mosaic check (the ``ConfidentialityClassifier`` port).

One place decides confidentiality for every package: stories and POVs, voice exemplars and
gate 3 call ``services.classifier``, which is a :class:`StoriesClassifier` in production.

Per text, the verdict merges two layers:

1. :func:`~blogwriter.stories.intake.local_findings` (deterministic: blocklist, e-mail, phone,
   IP addresses, handles, figures, names). Links are reported as ``warn`` here: public
   citations are normal in articles (gate 7 polices links) and the model judges whether a link
   identifies anyone; the intake routes still refuse links in story text.
2. ``Task.FACT_CHECK`` at ``Sensitivity.ANONYMIZED``, tool-free, structured output: the
   confidential details and the **mosaic** check (industry, counts, vendor stack, geography,
   timeline). Several texts share one call; a text the model skipped is asked about alone,
   then reported as ``classifier-incomplete``.

A text with any blocklist finding (or an unconfigured blocklist) is never sent to the model:
it fails on its local findings. Contact details and handles are masked before the call.
``passed`` is true only when no finding is ``block``; findings are capped at 50 per text
(blocking first). Model findings carry a span when their excerpt is found in the text, and a
generic message per category: never the excerpt, the text or a term.

Raises :class:`~blogwriter.domain.confidentiality.AiUseNotConfirmed` before any model call
when the owner has not confirmed the AI-use policy, :class:`ServiceUnavailable` (``ai``)
without a model, and lets ``BrakeEngaged`` through (jobs wrap calls in ``defer_on_brake``).
Callers treat all of these as "could not run", never as a pass.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Final, Literal

from pydantic import Field

from blogwriter.ai.ledger import Origin
from blogwriter.ai.types import Message, Request, Sensitivity, Task
from blogwriter.domain.api import ApiModel
from blogwriter.domain.base import sha256_hex, to_iso
from blogwriter.domain.common import (
    MAX_STORED_FINDINGS,
    ClassifierCheck,
    Finding,
    ServedByRecord,
    cap_findings,
)
from blogwriter.domain.confidentiality import require_ai_use
from blogwriter.domain.docstore import read_singleton
from blogwriter.domain.ports import ModelRunner, Services, ServiceUnavailable
from blogwriter.domain.settings import Blocklist, EmployerPolicy
from blogwriter.stories._logging import logger
from blogwriter.stories._prompts import CLASSIFY, fenced, load_prompt
from blogwriter.stories.intake import (
    EMAIL_CODE,
    HANDLE_CODE,
    IP_CODE,
    PHONE_CODE,
    URL_CODE,
    is_blocklist_finding,
    local_findings,
    mask_text,
)

__all__ = [
    "CLASSIFIER_STEP",
    "INCOMPLETE_CODE",
    "MODEL_CODES",
    "REFUSED_CODE",
    "ClassifierFinding",
    "ClassifierReply",
    "ClassifierVerdict",
    "StoriesClassifier",
]

CLASSIFIER_STEP: Final = "classify"
REFUSED_CODE: Final = "classifier-refused"
INCOMPLETE_CODE: Final = "classifier-incomplete"
_VERDICT_CODE: Final = "confidential-detail"

type Category = Literal[
    "client_identity", "person", "internal_system", "non_public_figure", "mosaic", "other"
]

MODEL_CODES: Final[dict[str, tuple[str, str]]] = {
    "client_identity": ("client-identity", "May identify a client or the employer."),
    "person": ("person-identity", "May identify a private person."),
    "internal_system": (
        "internal-name",
        "Names an internal project, system, host or ticket. Describe it instead.",
    ),
    "non_public_figure": (
        "non-public-figure",
        "An exact internal figure. Round it into a bucket unless it is public.",
    ),
    "mosaic": (
        "mosaic-risk",
        "Together these details (industry, size, place, vendors, timeline) could identify a "
        "client or an engagement. Make some of them coarser.",
    ),
    "other": (_VERDICT_CODE, "May expose confidential information."),
}
"""Category -> (finding code, owner-facing message). Messages never quote the text."""

_MASKED_CODES: Final = frozenset({EMAIL_CODE, PHONE_CODE, IP_CODE, HANDLE_CODE})
_LINK_MESSAGE: Final = "Contains a web link. Keep it only if it is public."
_MAX_EXCERPT: Final = 300
_LOCAL_IN_THREAD_CHARS: Final = 20_000


class ClassifierFinding(ApiModel):
    """One problem the model found (structured output)."""

    category: Category
    severity: Literal["block", "warn"]
    excerpt: str = Field(default="", max_length=_MAX_EXCERPT)


class ClassifierVerdict(ApiModel):
    """The model's verdict on one text (``id`` as in ``<text id="t1">``)."""

    id: str = Field(min_length=1, max_length=16)
    verdict: Literal["pass", "fail"]
    findings: list[ClassifierFinding] = Field(
        default_factory=list[ClassifierFinding], max_length=20
    )


class ClassifierReply(ApiModel):
    """The structured output of one classifier call."""

    results: list[ClassifierVerdict] = Field(default_factory=list[ClassifierVerdict], max_length=40)


@dataclass(frozen=True, slots=True)
class _Item:
    index: int
    text: str
    sent: str


@dataclass(frozen=True, slots=True)
class _Outcome:
    record: ServedByRecord
    verdict: ClassifierVerdict | None = None
    refused: bool = False


def _batches(items: Sequence[_Item], *, max_texts: int, max_chars: int) -> list[list[_Item]]:
    batches: list[list[_Item]] = []
    current: list[_Item] = []
    size = 0
    for item in items:
        length = len(item.sent)
        if current and (len(current) >= max_texts or size + length > max_chars):
            batches.append(current)
            current, size = [], 0
        current.append(item)
        size += length
    if current:
        batches.append(current)
    return batches


def _locate(text: str, excerpt: str) -> tuple[int, int] | None:
    needle = excerpt.strip()
    if len(needle) < 3:
        return None
    found = text.find(needle)
    if found < 0:
        found = text.casefold().find(needle.casefold())
        if found < 0 or len(text.casefold()) != len(text):
            return None
    return found, found + len(needle)


def _model_finding(text: str, finding: ClassifierFinding) -> Finding:
    code, message = MODEL_CODES[finding.category]
    span = _locate(text, finding.excerpt)
    return Finding(
        code=code,
        severity=finding.severity,
        message=message,
        start=span[0] if span else None,
        end=span[1] if span else None,
        overridable=True,
    )


def _adjust_local(finding: Finding) -> Finding:
    """Local findings as the classifier reports them: links warn (see the module docs)."""
    if finding.code == URL_CODE and finding.severity == "block":
        return finding.model_copy(
            update={"severity": "warn", "message": _LINK_MESSAGE, "overridable": True}
        )
    return finding


_REFUSED: Final = Finding(
    code=REFUSED_CODE,
    severity="block",
    message="The classifier declined to review this text. Try again, or rephrase it.",
)
_INCOMPLETE: Final = Finding(
    code=INCOMPLETE_CODE,
    severity="block",
    message="The classifier returned no verdict for this text. Try again.",
)
_FAILED: Final = Finding(
    code=_VERDICT_CODE,
    severity="block",
    message="The classifier judged this text confidential.",
    overridable=True,
)


def _verdict(
    text: str,
    local: Sequence[Finding],
    outcome: _Outcome | None,
    *,
    stamp: str,
    sent: bool,
) -> ClassifierCheck:
    findings = [_adjust_local(finding) for finding in local]
    record: ServedByRecord | None = None
    if sent and outcome is None:
        findings.append(_INCOMPLETE.model_copy())
    elif sent and outcome is not None:
        record = outcome.record
        verdict = outcome.verdict
        if outcome.refused or verdict is None:
            findings.append(_REFUSED.model_copy())
        else:
            model = [_model_finding(text, item) for item in verdict.findings]
            findings.extend(model)
            if verdict.verdict == "fail" and not any(item.severity == "block" for item in model):
                findings.append(_FAILED.model_copy())
    passed = not any(finding.severity == "block" for finding in findings)
    kept, _ = cap_findings(findings, MAX_STORED_FINDINGS)
    return ClassifierCheck(
        passed=passed,
        checked_at=stamp,
        findings=kept,
        served_by=record,
        content_sha256=sha256_hex(text),
    )


def _local_all(texts: Sequence[str], blocklist: Blocklist) -> list[list[Finding]]:
    return [local_findings(text, blocklist, markdown=True) for text in texts]


class StoriesClassifier:
    """:class:`~blogwriter.domain.ports.ConfidentialityClassifier` over ``services.ai``.

    ``services`` is called on every :meth:`classify` (``lambda: StateServices(app)`` in
    production), so the classifier always sees the current store, model and settings.
    """

    def __init__(
        self,
        services: Callable[[], Services],
        *,
        batch_texts: int = 8,
        batch_chars: int = 24_000,
    ) -> None:
        if batch_texts < 1 or batch_chars < 1:
            raise ValueError("batches need room for at least one text")
        self._services = services
        self._batch_texts = batch_texts
        self._batch_chars = batch_chars

    async def classify(
        self, texts: Sequence[str], *, origin: Origin, job_id: str | None = None
    ) -> list[ClassifierCheck]:
        """One :class:`ClassifierCheck` per text, in order (see the module docs)."""
        services = self._services()
        store = services.store
        stamp = to_iso(services.clock.now())
        policy, _ = await read_singleton(store, EmployerPolicy, now=stamp)
        require_ai_use(policy)
        runner = services.ai
        if runner is None:
            raise ServiceUnavailable("ai")
        blocklist, _ = await read_singleton(store, Blocklist, now=stamp)
        texts = list(texts)
        if sum(len(text) for text in texts) > _LOCAL_IN_THREAD_CHARS:
            local = await asyncio.to_thread(_local_all, texts, blocklist)
        else:
            local = _local_all(texts, blocklist)
        items: list[_Item] = []
        for index, (text, found) in enumerate(zip(texts, local, strict=True)):
            if not text.strip() or any(is_blocklist_finding(f) for f in found):
                continue  # blank: nothing to judge; blocklisted: never sent to a model
            masked = mask_text(text, [f for f in found if f.code in _MASKED_CODES], blocklist)
            items.append(_Item(index=index, text=text, sent=masked))
        started = time.monotonic()
        outcomes: dict[int, _Outcome] = {}
        batches = _batches(items, max_texts=self._batch_texts, max_chars=self._batch_chars)
        calls = 0
        for batch in batches:
            outcomes.update(await self._run(runner, batch, origin, job_id, stamp))
            calls += 1
        missing = [item for item in items if item.index not in outcomes]
        for item in missing:
            outcomes.update(await self._run(runner, [item], origin, job_id, stamp))
            calls += 1
        sent = {item.index for item in items}
        checks = [
            _verdict(
                text,
                local[index],
                outcomes.get(index),
                stamp=stamp,
                sent=index in sent,
            )
            for index, text in enumerate(texts)
        ]
        logger.info(
            "stories_classified",
            extra={
                "texts": len(texts),
                "batches": calls,
                "count": sum(1 for check in checks if check.passed),
                "job_id": job_id,
                "duration_ms": round((time.monotonic() - started) * 1000, 1),
            },
        )
        return checks

    async def _run(
        self,
        runner: ModelRunner,
        batch: Sequence[_Item],
        origin: Origin,
        job_id: str | None,
        stamp: str,
    ) -> dict[int, _Outcome]:
        keys = {f"t{position + 1}": item for position, item in enumerate(batch)}
        body = "\n\n".join(fenced("text", item.sent, id=key) for key, item in keys.items())
        prompt = load_prompt(CLASSIFY)
        request = Request(
            system=prompt.text,
            messages=(Message.user(f"Review each text.\n\n{body}"),),
            sensitivity=Sensitivity.ANONYMIZED,
            output_schema=ClassifierReply,
        )
        result = await runner.run(Task.FACT_CHECK, request, origin=origin, job_id=job_id)
        record = ServedByRecord.from_served_by(
            result.served_by,
            step=CLASSIFIER_STEP,
            at=stamp,
            refused=result.refused or bool(result.refusals),
            cost_usd=float(result.cost_usd) / len(batch),
            prompt_version=prompt.version,
        )
        if result.refused:
            return {item.index: _Outcome(record=record, refused=True) for item in batch}
        reply = result.value if isinstance(result.value, ClassifierReply) else None
        if reply is None:
            return {}
        outcomes: dict[int, _Outcome] = {}
        for verdict in reply.results:
            item = keys.get(verdict.id.strip())
            if item is None or item.index in outcomes:
                continue
            outcomes[item.index] = _Outcome(record=record, verdict=verdict)
        return outcomes
