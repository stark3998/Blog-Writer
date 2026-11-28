"""The confidentiality classifier: the port, the AI-use gate, local layer, batching, verdicts."""

from __future__ import annotations

import pytest

from blogwriter.ai.ledger import BrakeEngaged
from blogwriter.ai.types import Sensitivity, Task
from blogwriter.domain.base import sha256_hex
from blogwriter.domain.common import MAX_STORED_FINDINGS
from blogwriter.domain.confidentiality import AiUseNotConfirmed
from blogwriter.domain.ports import ConfidentialityClassifier, ServiceUnavailable
from blogwriter.domain.testing import (
    SAMPLE_NOW,
    FakeServices,
    ScriptedReply,
    make_services,
)
from blogwriter.stories import StoriesClassifier
from blogwriter.stories.classify import ClassifierReply
from blogwriter.stories.testing import classifier_reply, flagged, save_policies

# ---------------------------------------------------------------------------------------------
# Static conformance: pyright checks this assignment (the function is never called).
# ---------------------------------------------------------------------------------------------


def _stories_classifier_is_a_classifier(real: StoriesClassifier) -> ConfidentialityClassifier:
    return real


def test_the_classifier_satisfies_the_port() -> None:
    assert callable(_stories_classifier_is_a_classifier)


def classifier_for(services: FakeServices, **options: int) -> StoriesClassifier:
    return StoriesClassifier(lambda: services, **options)


def sent_texts(services: FakeServices) -> list[str]:
    assert services.ai is not None
    return [
        "\n".join(message.text for message in call.request.messages)
        for call in services.ai.calls_for(Task.FACT_CHECK)
    ]


async def test_a_passing_text(services: FakeServices) -> None:
    assert services.ai is not None
    services.ai.add(Task.FACT_CHECK, classifier_reply("pass"))
    text = "I moved 2,000+ apps from Okta to Microsoft Entra ID in about a year."
    (check,) = await classifier_for(services).classify([text], origin="owner", job_id="job-1")
    assert check.passed is True
    assert check.findings == []
    assert check.content_sha256 == sha256_hex(text)
    assert check.checked_at == SAMPLE_NOW
    assert check.served_by is not None
    assert check.served_by.step == "classify"
    assert check.served_by.task == "fact_check"
    assert check.served_by.prompt_version == "classify.v1"
    (call,) = services.ai.calls
    assert call.task is Task.FACT_CHECK
    assert call.request.sensitivity is Sensitivity.ANONYMIZED
    assert call.request.tools == ()
    assert call.request.output_schema is ClassifierReply
    assert (call.origin, call.job_id) == ("owner", "job-1")


async def test_ai_use_is_required_before_any_model_call() -> None:
    services = make_services()
    await save_policies(services.store, ai_use=False)
    with pytest.raises(AiUseNotConfirmed):
        await classifier_for(services).classify(["anything"], origin="owner")
    assert services.ai is not None
    assert services.ai.calls == []


async def test_no_model_is_service_unavailable() -> None:
    services = make_services(ai=None)
    await save_policies(services.store)
    with pytest.raises(ServiceUnavailable) as raised:
        await classifier_for(services).classify(["anything"], origin="owner")
    assert raised.value.name == "ai"


async def test_blocklisted_texts_are_never_sent(services: FakeServices) -> None:
    assert services.ai is not None
    services.ai.add(Task.FACT_CHECK, classifier_reply("pass"))
    texts = ["Contoso's tenant moved first.", "A clean sentence about tenants."]
    blocked, clean = await classifier_for(services).classify(texts, origin="owner")
    assert blocked.passed is False
    assert [f.code for f in blocked.findings] == ["blocklisted-term"]
    assert blocked.served_by is None
    assert clean.passed is True
    (sent,) = sent_texts(services)
    assert "Contoso" not in sent
    assert "A clean sentence about tenants." in sent


async def test_an_unconfigured_blocklist_sends_nothing() -> None:
    services = make_services()
    await save_policies(services.store, blocklist=False)
    checks = await classifier_for(services).classify(["one", "two"], origin="owner")
    assert [c.passed for c in checks] == [False, False]
    assert all(c.findings[0].code == "blocklist-not-configured" for c in checks)
    assert services.ai is not None
    assert services.ai.calls == []


async def test_model_findings_get_spans_and_generic_messages(services: FakeServices) -> None:
    assert services.ai is not None
    text = "A regional credit union in a small state with 41 branches moved to Entra in 2024."
    services.ai.add(
        Task.FACT_CHECK,
        classifier_reply(
            [
                flagged("mosaic", "regional credit union in a small state with 41 branches"),
                flagged("non_public_figure", "a phrase the text does not contain", severity="warn"),
            ]
        ),
    )
    (check,) = await classifier_for(services).classify([text], origin="background")
    assert check.passed is False
    mosaic = next(f for f in check.findings if f.code == "mosaic-risk")
    assert mosaic.severity == "block"
    assert mosaic.start is not None
    assert mosaic.end is not None
    assert text[mosaic.start : mosaic.end].startswith("regional credit union")
    assert "credit union" not in mosaic.message
    figure = next(f for f in check.findings if f.code == "non-public-figure")
    assert figure.start is None
    assert figure.severity == "warn"


async def test_a_fail_verdict_without_findings_still_fails(services: FakeServices) -> None:
    assert services.ai is not None
    services.ai.add(Task.FACT_CHECK, classifier_reply("fail"))
    (check,) = await classifier_for(services).classify(["Some text."], origin="owner")
    assert check.passed is False
    assert [f.code for f in check.findings] == ["confidential-detail"]


async def test_a_refusal_is_never_a_pass(services: FakeServices) -> None:
    assert services.ai is not None
    services.ai.add(Task.FACT_CHECK, ScriptedReply(refused=True))
    checks = await classifier_for(services).classify(["One.", "Two."], origin="owner")
    for check in checks:
        assert check.passed is False
        assert [f.code for f in check.findings] == ["classifier-refused"]
        assert check.served_by is not None
        assert check.served_by.refused


async def test_a_skipped_text_is_asked_alone_then_reported_incomplete(
    services: FakeServices,
) -> None:
    assert services.ai is not None
    services.ai.add(
        Task.FACT_CHECK,
        classifier_reply("pass", ids=["t1"]),  # t2 missing from the batch reply
        classifier_reply("pass", ids=["t9"]),  # the retry answers the wrong id
    )
    first, second = await classifier_for(services).classify(["One.", "Two."], origin="owner")
    assert first.passed is True
    assert second.passed is False
    assert [f.code for f in second.findings] == ["classifier-incomplete"]
    assert len(services.ai.calls) == 2


async def test_texts_are_batched(services: FakeServices) -> None:
    assert services.ai is not None
    services.ai.add(
        Task.FACT_CHECK,
        classifier_reply("pass", "pass", "pass", "pass"),
        classifier_reply("pass", "pass", "pass", "pass"),
        classifier_reply("pass", "fail"),
    )
    texts = [f"Text number {n} about tenants." for n in range(10)]
    checks = await classifier_for(services, batch_texts=4).classify(texts, origin="owner")
    assert [c.passed for c in checks] == [True] * 9 + [False]
    assert len(services.ai.calls) == 3
    assert sent_texts(services)[0].count("<text id=") == 4


async def test_contact_details_are_masked_and_links_only_warn(services: FakeServices) -> None:
    assert services.ai is not None
    services.ai.add(Task.FACT_CHECK, classifier_reply("pass"))
    text = (
        "Per https://learn.microsoft.com/entra/identity/ we enabled it; "
        "ask avery.t@fabrikam-bank.com or @avery_t."
    )
    (check,) = await classifier_for(services).classify([text], origin="owner")
    (sent,) = sent_texts(services)
    assert "avery.t@fabrikam-bank.com" not in sent
    assert "@avery_t" not in sent
    assert "https://learn.microsoft.com/entra/identity/" in sent
    severities = {f.code: f.severity for f in check.findings}
    assert severities["url"] == "warn"
    assert severities["email-address"] == "block"
    assert check.passed is False


async def test_findings_are_capped_with_blockers_first(services: FakeServices) -> None:
    assert services.ai is not None
    services.ai.add(Task.FACT_CHECK, classifier_reply("pass"))
    figures = " ".join(f"{n},137 apps" for n in range(2, 80))
    text = f"{figures} and @avery_t."
    (check,) = await classifier_for(services).classify([text], origin="owner")
    assert len(check.findings) == MAX_STORED_FINDINGS
    assert check.findings[0].code == "handle"
    assert check.passed is False


async def test_blank_texts_pass_without_a_call(services: FakeServices) -> None:
    (check,) = await classifier_for(services).classify(["   "], origin="owner")
    assert check.passed is True
    assert services.ai is not None
    assert services.ai.calls == []


async def test_the_brake_reaches_background_callers(services: FakeServices) -> None:
    assert services.ai is not None
    services.ai.add(
        Task.FACT_CHECK, ScriptedReply(error=BrakeEngaged(None, reason="ledger_unavailable"))
    )
    with pytest.raises(BrakeEngaged):
        await classifier_for(services).classify(["Some text."], origin="background")


async def test_long_texts_are_checked_off_the_event_loop(services: FakeServices) -> None:
    assert services.ai is not None
    services.ai.add(Task.FACT_CHECK, classifier_reply("pass"), classifier_reply("pass"))
    long_text = "Plain words about identity. " * 1500
    checks = await classifier_for(services).classify([long_text, long_text], origin="owner")
    assert [c.passed for c in checks] == [True, True]


def test_batches_need_room() -> None:
    services = make_services()
    with pytest.raises(ValueError, match="at least one text"):
        StoriesClassifier(lambda: services, batch_texts=0)
