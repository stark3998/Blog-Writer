"""The anonymizer proposal: one private, tool-free, owner call on locally masked text."""

from __future__ import annotations

import logging

import pytest

from blogwriter.ai.types import Sensitivity, Task
from blogwriter.domain.confidentiality import BlocklistNotConfigured
from blogwriter.domain.docstore import read_singleton, save_singleton
from blogwriter.domain.settings import PillarSettings
from blogwriter.domain.testing import (
    SAMPLE_NOW,
    FakeServices,
    ScriptedModelRunner,
    ScriptedReply,
    make_services,
)
from blogwriter.stories import propose
from blogwriter.stories.testing import save_policies

RAW = (
    "Last quarter Contoso asked me to move 2,137 apps off Falcon. "
    "Avery Testperson (avery.t@fabrikam-bank.com) owned the cutover."
)
PROPOSAL = "Last year a client asked me to move 2,000+ apps off an internal platform."


def request_text(services: FakeServices) -> str:
    assert services.ai is not None
    (call,) = services.ai.calls_for(Task.ANONYMIZE)
    return call.request.system + "\n" + "\n".join(m.text for m in call.request.messages)


async def test_the_proposal_comes_from_one_private_owner_call(services: FakeServices) -> None:
    assert services.ai is not None
    services.ai.add(Task.ANONYMIZE, PROPOSAL)
    result = await propose(RAW, purpose="interview_answer", services=services, pillar="identity")
    assert result.proposal == PROPOSAL
    assert result.reason is None
    assert result.anonymizer_used
    codes = [finding.code for finding in result.findings]
    assert codes.count("blocklisted-term") == 2
    assert {"email-address", "exact-figure", "possible-name"} <= set(codes)
    (call,) = services.ai.calls
    assert call.task is Task.ANONYMIZE
    assert call.request.sensitivity is Sensitivity.PRIVATE
    assert call.request.tools == ()
    assert call.request.output_schema is None
    assert call.origin == "owner"
    assert call.job_id is None


async def test_blocklisted_terms_and_contact_details_never_reach_the_model(
    services: FakeServices,
) -> None:
    assert services.ai is not None
    services.ai.add(Task.ANONYMIZE, PROPOSAL)
    await propose(RAW, purpose="story", services=services)
    sent = request_text(services)
    for secret in ("Contoso", "Falcon", "avery.t@fabrikam-bank.com"):
        assert secret not in sent
    assert "[client]" in sent
    assert "[internal project]" in sent
    assert "[email address]" in sent
    assert "Purpose: story" in sent
    assert "<owner_text>" in sent


async def test_pillar_vocabulary_is_sent_as_public_terms(services: FakeServices) -> None:
    assert services.ai is not None
    pillars, etag = await read_singleton(services.store, PillarSettings, now=SAMPLE_NOW)
    identity = next(p for p in pillars.pillars if p.id == "identity")
    changed = identity.model_copy(update={"vocabulary": ["Conditional Access", "Contoso SSO"]})
    updated = pillars.model_copy(
        update={"pillars": [changed if p.id == "identity" else p for p in pillars.pillars]}
    )
    await save_singleton(services.store, updated, if_match=etag)
    services.ai.add(Task.ANONYMIZE, PROPOSAL)
    await propose(
        "We used Conditional Access.", purpose="pov", services=services, pillar="identity"
    )
    sent = request_text(services)
    assert "Conditional Access" in sent
    assert "Contoso" not in sent  # a blocklisted vocabulary term is never sent


async def test_strict_mode_makes_no_model_call() -> None:
    strict = ScriptedModelRunner(allow_private=False)
    services = make_services(ai=strict)
    await save_policies(services.store)
    result = await propose(RAW, purpose="interview_answer", services=services)
    assert result.proposal is None
    assert result.reason == "strict_mode"
    assert not result.anonymizer_used
    assert strict.calls == []
    assert result.findings  # the owner still gets the local findings


async def test_no_call_without_the_ai_use_confirmation() -> None:
    services = make_services()
    await save_policies(services.store, ai_use=False)
    result = await propose(RAW, purpose="story", services=services)
    assert result.reason == "ai_use_not_confirmed"
    assert result.proposal is None
    assert services.ai is not None
    assert services.ai.calls == []


async def test_no_call_without_a_model() -> None:
    services = make_services(ai=None)
    await save_policies(services.store)
    result = await propose(RAW, purpose="story", services=services)
    assert result.reason == "ai_unavailable"
    assert not result.anonymizer_used


async def test_an_unconfigured_blocklist_refuses_before_anything() -> None:
    services = make_services()
    await save_policies(services.store, blocklist=False)
    with pytest.raises(BlocklistNotConfigured):
        await propose(RAW, purpose="story", services=services)
    assert services.ai is not None
    assert services.ai.calls == []


async def test_a_refusal_gives_no_proposal(services: FakeServices) -> None:
    assert services.ai is not None
    services.ai.add(Task.ANONYMIZE, ScriptedReply(refused=True))
    result = await propose(RAW, purpose="take", services=services)
    assert result.proposal is None
    assert result.reason == "refused"
    assert result.anonymizer_used


async def test_an_empty_reply_counts_as_a_refusal(services: FakeServices) -> None:
    assert services.ai is not None
    services.ai.add(Task.ANONYMIZE, '  ""  ')
    result = await propose(RAW, purpose="take", services=services)
    assert result.reason == "refused"


async def test_identifying_details_in_the_proposal_are_masked_again(
    services: FakeServices,
) -> None:
    assert services.ai is not None
    services.ai.add(Task.ANONYMIZE, '"Ask Contoso ops at ops@fabrikam-bank.com."')
    result = await propose(RAW, purpose="idea_note", services=services)
    assert result.proposal == "Ask [client] ops at [email address]."


async def test_provider_errors_leave_the_local_findings_and_log_no_content(
    services: FakeServices, caplog: pytest.LogCaptureFixture
) -> None:
    assert services.ai is not None
    services.ai.add(Task.ANONYMIZE, ScriptedReply(error=RuntimeError(RAW)))
    with caplog.at_level(logging.DEBUG):
        result = await propose(RAW, purpose="voice_sample", services=services)
    assert result.reason == "ai_unavailable"
    assert result.findings
    assert caplog.records
    for record in caplog.records:
        assert "Contoso" not in record.getMessage()
        assert "Contoso" not in str(record.__dict__)
