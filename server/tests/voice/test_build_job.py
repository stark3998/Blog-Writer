"""``voice.build-guide`` on a real runner: flagged passages are never exemplars, the classifier
gates exemplars, versions are monotonic and a resumed step never creates a second version."""

from __future__ import annotations

import json
from dataclasses import dataclass

import pytest
from pydantic import JsonValue

from blogwriter.ai.types import Sensitivity, Task
from blogwriter.domain.confidentiality import AiUseNotConfirmed
from blogwriter.domain.docstore import create_doc, find_docs, read_doc
from blogwriter.domain.jobtypes import VOICE_BUILD_GUIDE
from blogwriter.domain.testing import (
    FakeClassifier,
    FakeServices,
    ScriptedModelRunner,
    ScriptedReply,
    sample_document,
)
from blogwriter.domain.voice import StyleGuide, VoiceSample
from blogwriter.jobs import Job
from blogwriter.voice.corpus import add_sample
from blogwriter.voice.guide import (
    BASELINE_BANNED_PHRASES,
    ExemplarPick,
    GuideDraft,
    VocabularyItem,
    activate,
)
from blogwriter.voice.testing import (
    LINKEDIN_POSTS,
    MarkerSlopChecker,
    enqueue_job,
    preset_steps,
    run_job,
    run_until_settled,
    seed_policy,
)

CURLY = "\u2019"
TELL_PARAGRAPH = f"Let{CURLY}s dive in on agent identity."
SLOP_MARKER = "Here is the reveal:"
CONFIDENTIAL = "Fabrikam-internal"
OWNER: dict[str, JsonValue] = {"origin": "owner"}


@dataclass(frozen=True)
class Corpus:
    tell: str
    slop: str
    secret: str
    clean1: str
    clean2: str
    clean3: str

    @property
    def flagged(self) -> set[str]:
        return {self.tell, self.slop}

    @property
    def all(self) -> list[str]:
        return [self.tell, self.slop, self.secret, self.clean1, self.clean2, self.clean3]


def runner(services: FakeServices) -> ScriptedModelRunner:
    assert services.ai is not None
    return services.ai


async def build_corpus(services: FakeServices) -> Corpus:
    """Five LinkedIn posts (one with an AI tell, one with a slop marker, one the classifier
    flags, two clean) and a clean portfolio post."""
    await seed_policy(services)
    services.slop = MarkerSlopChecker({SLOP_MARKER: "colon-reveal"})
    services.classifier = FakeClassifier(flag=CONFIDENTIAL)
    now = services.clock.now()

    async def add(text: str, source: str = "linkedin_post", unassisted: bool = False) -> str:
        stored = await add_sample(
            services.store,
            source_type="linkedin_post" if source == "linkedin_post" else "portfolio_post",
            text=text,
            now=now,
            slop=services.slop,
            owner_unassisted=unassisted,
        )
        return stored.doc.id

    return Corpus(
        tell=await add(f"{TELL_PARAGRAPH}\n\n{LINKEDIN_POSTS[0]}"),
        slop=await add(f"{LINKEDIN_POSTS[1]}\n\n{SLOP_MARKER} the token was the problem."),
        secret=await add(f"{LINKEDIN_POSTS[2]}\n\nThe {CONFIDENTIAL} rollout taught us that."),
        clean1=await add(LINKEDIN_POSTS[3], unassisted=True),
        clean2=await add(LINKEDIN_POSTS[4]),
        clean3=await add(LINKEDIN_POSTS[5], source="portfolio_post"),
    )


def draft(corpus: Corpus, **overrides: object) -> GuideDraft:
    values: dict[str, object] = {
        "body": "## Openings\n\nStarts with the incident and the month.",
        "prefer": [
            "Give the number with its unit, then what it changed.",
            "give the number with its unit, then what it changed.",
        ],
        "avoid": ["Do not open with a question to the reader."],
        "banned_phrases": ["synergy", "Let's dive in"],
        "vocabulary": [
            VocabularyItem(term="identity provider", note="spelled out"),
            VocabularyItem(term="Identity Provider", note="a duplicate"),
        ],
        "exemplars": [
            ExemplarPick(sample_id=corpus.tell, excerpt=LINKEDIN_POSTS[0][:200]),
            ExemplarPick(sample_id=corpus.slop, excerpt=LINKEDIN_POSTS[1][:200]),
            ExemplarPick(sample_id=corpus.secret, excerpt=LINKEDIN_POSTS[2][:200]),
            ExemplarPick(
                sample_id=corpus.clean1,
                excerpt=(
                    "A client asked why their MFA prompts doubled after the cut-over. "
                    "The answer was a conditional access policy"
                ),
            ),
            ExemplarPick(sample_id=corpus.clean2, excerpt="Prompt injection is basically a bug."),
            ExemplarPick(sample_id="sample-0000000000000000000000000099", excerpt="Unknown."),
        ],
    }
    values.update(overrides)
    return GuideDraft.model_validate(values)


def job_json(job: Job) -> str:
    return json.dumps(job.to_body())


async def test_flagged_passages_are_never_exemplars(services: FakeServices) -> None:
    corpus = await build_corpus(services)
    runner(services).add(Task.SYNTHESIS, draft(corpus))
    job = await run_job(services, VOICE_BUILD_GUIDE, OWNER, job_id="build-1")
    assert job.status == "succeeded", job.error
    assert job.result == {"guideId": "style-guide-0001", "version": 1, "exemplars": 3}
    guide = (await read_doc(services.store, StyleGuide, "style-guide-0001")).doc
    assert guide.status.value == "proposed"
    quoted = [exemplar.sample_id for exemplar in guide.exemplars]
    assert quoted == [corpus.clean1, corpus.clean2, corpus.clean3]
    assert not ({corpus.tell, corpus.slop, corpus.secret} & set(quoted))
    clean1 = guide.exemplars[0].excerpt
    assert clean1.startswith("A client asked why their MFA prompts doubled after the cut-over.\n\n")
    assert clean1.endswith("a conditional access policy")
    for exemplar in guide.exemplars:
        sample = await read_doc(services.store, VoiceSample, exemplar.sample_id)
        assert exemplar.excerpt in sample.doc.text
    assert guide.rules.prefer == ["Give the number with its unit, then what it changed."]
    assert guide.rules.vocabulary == {"identity provider": "spelled out"}
    banned = [phrase.casefold() for phrase in guide.rules.banned_phrases]
    assert "synergy" in banned
    assert banned.count("let's dive in") == 1
    assert all(phrase.casefold() in banned for phrase in BASELINE_BANNED_PHRASES)
    assert guide.based_on_version is None
    assert sorted(guide.source_sample_ids) == sorted(corpus.all)
    [record] = guide.served_by
    assert (record.step, record.task, record.prompt_version) == (
        "synthesize",
        "synthesis",
        "voice.guide.v1",
    )

    [call] = runner(services).calls_for(Task.SYNTHESIS)
    assert call.request.sensitivity is Sensitivity.ANONYMIZED
    assert call.request.tools == ()
    assert call.origin == "owner"
    assert call.job_id == "build-1"
    prompt = call.request.messages[0].text
    assert f"<flagged>\n{TELL_PARAGRAPH}\n</flagged>" in prompt
    assert f"<flagged>\n{SLOP_MARKER} the token was the problem.\n</flagged>" in prompt
    candidates = prompt.split("<exemplar_candidates>")[1].split("</exemplar_candidates>")[0]
    assert sorted(candidates.split()) == sorted([corpus.clean1, corpus.clean2, corpus.clean3])

    assert isinstance(services.classifier, FakeClassifier)
    classified = [text for texts, _origin in services.classifier.calls for text in texts]
    assert TELL_PARAGRAPH not in "".join(classified)
    assert SLOP_MARKER not in "".join(classified)
    assert any(CONFIDENTIAL in text for text in classified)
    assert all(origin == "owner" for _texts, origin in services.classifier.calls)

    body = job_json(await services.jobs.get("build-1"))
    for text in (LINKEDIN_POSTS[3][:40], TELL_PARAGRAPH, "Starts with the incident"):
        assert text not in body


async def test_versions_are_monotonic_and_build_on_the_active_guide(
    services: FakeServices,
) -> None:
    corpus = await build_corpus(services)
    runner(services).add(Task.SYNTHESIS, draft(corpus))
    await run_job(services, VOICE_BUILD_GUIDE, OWNER, job_id="build-1")
    first = await read_doc(services.store, StyleGuide, "style-guide-0001")
    await activate(services.store, first, if_match=first.etag, now=services.clock.now())
    runner(services).add(Task.SYNTHESIS, draft(corpus, banned_phrases=[]))
    second = await run_job(services, VOICE_BUILD_GUIDE, OWNER, job_id="build-2")
    assert second.result == {"guideId": "style-guide-0002", "version": 2, "exemplars": 3}
    guide = (await read_doc(services.store, StyleGuide, "style-guide-0002")).doc
    assert guide.based_on_version == 1
    assert "synergy" in guide.rules.banned_phrases  # a model never un-bans a phrase
    prompt = runner(services).calls_for(Task.SYNTHESIS)[1].request.messages[0].text
    assert '<current_rules version="1">' in prompt


async def test_a_resumed_synthesis_finds_its_version(services: FakeServices) -> None:
    corpus = await build_corpus(services)
    built = sample_document(
        StyleGuide,
        id=StyleGuide.id_for(1),
        version=1,
        body="# Voice\n\nFrom the first attempt.",
        served_by=[
            {
                "step": "synthesize",
                "at": "2026-11-26T12:00:00.000000Z",
                "task": "synthesis",
                "model": "fake-model",
                "deployment": "fake-deployment",
                "provider": "openai",
            }
        ],
    )
    await create_doc(services.store, built)
    await enqueue_job(services, VOICE_BUILD_GUIDE, OWNER, job_id="build-resume")
    await preset_steps(
        services,
        "build-resume",
        done={"score": {"samples": 6}, "classify": {"verdicts": {}, "approved": []}},
        checkpoints={"synthesize": {"version": 1}},
    )
    job = await run_until_settled(services, "build-resume")
    assert job.status == "succeeded"
    assert job.result == {"guideId": "style-guide-0001", "version": 1, "exemplars": 0}
    assert runner(services).calls_for(Task.SYNTHESIS) == []
    assert corpus.clean1  # the corpus exists; nothing else was created
    assert len(await find_docs(services.store, StyleGuide)) == 1


async def test_a_version_taken_by_the_owner_meanwhile_is_not_reused(
    services: FakeServices,
) -> None:
    corpus = await build_corpus(services)
    owner_edit = sample_document(
        StyleGuide, id=StyleGuide.id_for(1), version=1, edited_by_owner=True
    )
    await create_doc(services.store, owner_edit)
    await enqueue_job(services, VOICE_BUILD_GUIDE, OWNER, job_id="build-race")
    await preset_steps(services, "build-race", done={}, checkpoints={"synthesize": {"version": 1}})
    runner(services).add(Task.SYNTHESIS, draft(corpus))
    job = await run_until_settled(services, "build-race")
    assert job.result is not None
    assert job.status == "succeeded"
    assert isinstance(job.result, dict)
    assert job.result["guideId"] == "style-guide-0002"


async def test_a_blocklisted_sample_stops_the_build_before_any_model_call(
    services: FakeServices,
) -> None:
    corpus = await build_corpus(services)
    await seed_policy(services, terms=["Fabrikam-internal"])
    job = await run_job(services, VOICE_BUILD_GUIDE, OWNER, job_id="build-hit")
    assert job.status == "failed"
    assert job.error is not None
    assert job.error.code == "blocklist_hit"
    assert job.error.detail is not None
    assert corpus.secret in job.error.detail
    assert "Fabrikam" not in job.error.detail
    assert runner(services).calls_for(Task.SYNTHESIS) == []


async def test_a_blocklisted_guide_is_not_saved(services: FakeServices) -> None:
    corpus = await build_corpus(services)
    await seed_policy(services, terms=["Northwind"])
    runner(services).add(Task.SYNTHESIS, draft(corpus, body="Write like the Northwind posts."))
    job = await run_job(services, VOICE_BUILD_GUIDE, OWNER, job_id="build-out")
    assert job.status == "failed"
    assert job.error is not None
    assert job.error.code == "blocklist_hit"
    assert await find_docs(services.store, StyleGuide) == []


@pytest.mark.parametrize("where", ["policy", "classifier"])
async def test_no_ai_use_confirmation_means_no_model_call(
    services: FakeServices, where: str
) -> None:
    await build_corpus(services)
    if where == "policy":
        await seed_policy(services, ai_use_confirmed=False)
    else:
        services.classifier = FakeClassifier(error=AiUseNotConfirmed())
    job = await run_job(services, VOICE_BUILD_GUIDE, OWNER, job_id="build-ai-use")
    assert job.status == "failed"
    assert job.error is not None
    assert job.error.code == "ai_use_not_confirmed"
    assert runner(services).calls_for(Task.SYNTHESIS) == []


async def test_without_a_classifier_nothing_is_an_exemplar(services: FakeServices) -> None:
    corpus = await build_corpus(services)
    services.classifier = None
    runner(services).add(Task.SYNTHESIS, draft(corpus))
    job = await run_job(services, VOICE_BUILD_GUIDE, OWNER, job_id="build-no-classifier")
    assert job.status == "succeeded"
    guide = (await read_doc(services.store, StyleGuide, "style-guide-0001")).doc
    assert guide.exemplars == []
    prompt = runner(services).calls_for(Task.SYNTHESIS)[0].request.messages[0].text
    assert "<exemplar_candidates>\nnone\n</exemplar_candidates>" in prompt


async def test_without_slop_detectors_nothing_is_an_exemplar(services: FakeServices) -> None:
    corpus = await build_corpus(services)
    services.slop = None
    runner(services).add(Task.SYNTHESIS, draft(corpus))
    job = await run_job(services, VOICE_BUILD_GUIDE, OWNER, job_id="build-no-slop")
    assert job.status == "succeeded"
    assert job.steps[0].output is not None
    guide = (await read_doc(services.store, StyleGuide, "style-guide-0001")).doc
    assert guide.exemplars == []


async def test_too_few_posts_at_run_time_fail_the_build(services: FakeServices) -> None:
    corpus = await build_corpus(services)
    raw = await services.store.read("stories", corpus.clean2)
    await services.store.replace("stories", {**raw.body, "status": "excluded"}, if_match=raw.etag)
    job = await run_job(services, VOICE_BUILD_GUIDE, OWNER, job_id="build-few")
    assert job.status == "failed"
    assert job.error is not None
    assert job.error.code == "not_enough_samples"


async def test_a_provisional_build_succeeds_below_the_minimum_and_is_labelled(
    services: FakeServices,
) -> None:
    corpus = await build_corpus(services)
    raw = await services.store.read("stories", corpus.clean2)
    await services.store.replace("stories", {**raw.body, "status": "excluded"}, if_match=raw.etag)
    runner(services).add(Task.SYNTHESIS, draft(corpus))
    job = await run_job(
        services,
        VOICE_BUILD_GUIDE,
        {"origin": "owner", "provisional": True},
        job_id="build-provisional",
    )
    assert job.status == "succeeded"
    guide = (await read_doc(services.store, StyleGuide, "style-guide-0001")).doc
    assert guide.provisional is True


async def test_a_full_build_is_not_provisional(services: FakeServices) -> None:
    corpus = await build_corpus(services)
    runner(services).add(Task.SYNTHESIS, draft(corpus))
    job = await run_job(
        services, VOICE_BUILD_GUIDE, {"origin": "owner", "provisional": True}, job_id="b-full"
    )
    assert job.status == "succeeded"
    guide = (await read_doc(services.store, StyleGuide, "style-guide-0001")).doc
    assert guide.provisional is False


async def test_a_refusal_fails_the_build(services: FakeServices) -> None:
    await build_corpus(services)
    runner(services).add(Task.SYNTHESIS, ScriptedReply(refused=True))
    job = await run_job(services, VOICE_BUILD_GUIDE, OWNER, job_id="build-refused")
    assert job.status == "failed"
    assert job.error is not None
    assert job.error.code == "synthesis_refused"
    assert await find_docs(services.store, StyleGuide) == []
