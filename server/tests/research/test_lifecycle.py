"""The research lifecycle (``app.state.fetcher``), settings, prompts, job registration."""

from __future__ import annotations

import json
import logging
import re

import httpx
import pytest
from fastapi import FastAPI
from pydantic import ValidationError

from blogwriter.domain.jobtypes import RESEARCH_RUN, SCHEDULED_JOB_TYPES
from blogwriter.domain.ports import FetchRefused
from blogwriter.domain.settings import CadenceSettings
from blogwriter.domain.testing import SAMPLE_NOW, StaticHttp
from blogwriter.jobs import JobRegistry
from blogwriter.research import (
    FallbackFetcher,
    ResearchLifecycle,
    ResearchSettings,
    register_jobs,
    schedule_entries,
    webiq_state,
)
from blogwriter.research.prompting import PROMPT_FILES, load_prompt
from blogwriter.research.run import STEP_NAMES
from blogwriter.research.settings import DEFAULT_WEBIQ_SCOPE, research_settings
from blogwriter.research.testing import FakeCredential

URL = "https://learn.microsoft.com/en-us/entra/workload-id/agent-identities"


def browse_ok(seen: list[httpx.Request]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200, json={"url": URL, "title": "Agent identities", "content": "Text."}
        )

    return httpx.MockTransport(handler)


def app_with_egress() -> tuple[FastAPI, StaticHttp]:
    app = FastAPI()
    http = StaticHttp()
    http.add(URL, b"<main><p>Egress page.</p></main>", content_type="text/html")
    app.state.egress = http
    return app, http


async def test_without_web_iq_the_fetcher_is_egress_only(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    app, http = app_with_egress()
    lifecycle = ResearchLifecycle(ResearchSettings())
    await lifecycle.startup(app)
    fetcher = app.state.fetcher
    assert isinstance(fetcher, FallbackFetcher)
    assert not fetcher.webiq_enabled
    assert webiq_state(app) == "off"
    page = await fetcher.fetch(URL, allowed_urls=[URL])
    assert page.via == "egress"
    assert page.text == "Egress page."
    assert http.requested == [URL]
    reasons = [
        r.__dict__.get("reason")
        for r in caplog.records
        if r.getMessage() == "research_webiq_not_configured"
    ]
    assert reasons == ["unset"]
    await lifecycle.shutdown(app)
    assert app.state.fetcher is None


async def test_an_endpoint_without_a_scope_leaves_web_iq_off(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    app, _ = app_with_egress()
    lifecycle = ResearchLifecycle(ResearchSettings(webiq_endpoint="https://api.microsoft.ai"))
    await lifecycle.startup(app)
    assert not app.state.fetcher.webiq_enabled
    reasons = [
        r.__dict__.get("reason")
        for r in caplog.records
        if r.getMessage() == "research_webiq_not_configured"
    ]
    assert reasons == ["scope_missing"]
    await lifecycle.shutdown(app)


async def test_with_web_iq_the_managed_identity_token_is_used() -> None:
    app, http = app_with_egress()
    seen: list[httpx.Request] = []
    credential = FakeCredential()
    settings = ResearchSettings(
        webiq_endpoint="https://api.microsoft.ai/", webiq_scope=DEFAULT_WEBIQ_SCOPE
    )
    lifecycle = ResearchLifecycle(settings, credential=credential, transport=browse_ok(seen))
    await lifecycle.startup(app)
    assert webiq_state(app) == "on"

    page = await app.state.fetcher.fetch(URL, allowed_urls=[URL], query="agent identities")

    assert page.via == "webiq"
    assert http.requested == []
    (request,) = seen
    assert str(request.url) == "https://api.microsoft.ai/v3/browse"
    assert request.headers["authorization"] == "Bearer test-token"
    assert json.loads(request.content)["query"] == "agent identities"
    assert credential.scopes == [(DEFAULT_WEBIQ_SCOPE,)]
    await lifecycle.shutdown(app)
    assert not credential.closed  # injected credentials belong to the caller


async def test_a_missing_egress_service_is_unavailable() -> None:
    app = FastAPI()
    lifecycle = ResearchLifecycle(ResearchSettings())
    await lifecycle.startup(app)
    with pytest.raises(FetchRefused) as caught:
        await app.state.fetcher.fetch(URL, allowed_urls=[URL])
    assert caught.value.reason == "unavailable"
    await lifecycle.shutdown(app)
    assert webiq_state(FastAPI()) == "off"


# ---------------------------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------------------------


def test_settings_defaults_are_safe() -> None:
    settings = research_settings()
    assert settings.webiq_endpoint is None
    assert not settings.webiq_configured
    assert settings.browse_url is None
    assert (settings.webiq_qps, settings.max_sources, settings.max_tool_calls) == (15, 12, 8)
    assert (settings.fetch_timeout_seconds, settings.passage_max_chars) == (15, 3000)
    assert settings.quick_timeout_seconds == 90


def test_settings_read_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BW_RESEARCH_WEBIQ_ENDPOINT", "https://api.microsoft.ai")
    monkeypatch.setenv("BW_RESEARCH_WEBIQ_SCOPE", DEFAULT_WEBIQ_SCOPE)
    monkeypatch.setenv("BW_RESEARCH_MAX_SOURCES", "5")
    research_settings.cache_clear()
    settings = research_settings()
    assert settings.webiq_configured
    assert settings.max_sources == 5
    assert settings.browse_url == "https://api.microsoft.ai/v3/browse"


@pytest.mark.parametrize(
    ("endpoint", "browse"),
    [
        ("https://api.microsoft.ai", "https://api.microsoft.ai/v3/browse"),
        ("https://api.microsoft.ai/v3/", "https://api.microsoft.ai/v3/browse"),
        ("https://api.microsoft.ai/v3/browse", "https://api.microsoft.ai/v3/browse"),
        ("https://gateway.example.com/webiq", "https://gateway.example.com/webiq/v3/browse"),
    ],
)
def test_browse_url_from_the_endpoint(endpoint: str, browse: str) -> None:
    assert ResearchSettings(webiq_endpoint=endpoint).browse_url == browse


@pytest.mark.parametrize(
    "fields",
    [
        {"webiq_endpoint": "http://api.microsoft.ai"},
        {"webiq_endpoint": "https://api.microsoft.ai/?key=1"},
        {"webiq_endpoint": "https://user:pw@api.microsoft.ai"},
        {"webiq_scope": "https://api.microsoft.ai"},
        {"webiq_scope": "not a scope/.default"},
        {"max_sources": 0},
        {"passage_max_chars": 50},
        {"quick_timeout_seconds": 600},
        {"unknown": 1},
    ],
)
def test_bad_settings_fail(fields: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ResearchSettings.model_validate(fields)


# ---------------------------------------------------------------------------------------------
# Prompts, jobs and schedule
# ---------------------------------------------------------------------------------------------

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_GUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)


def test_prompts_are_versioned_and_treat_inputs_as_data() -> None:
    for name, version in PROMPT_FILES.items():
        prompt = load_prompt(name)
        assert prompt.version == version
        assert version.startswith(f"{name}.v")
        assert "data" in prompt.text
        assert not _EMAIL.search(prompt.text)
        assert not _GUID.search(prompt.text)
    assert "<<<PAGE" in load_prompt("extract").text
    assert "<<<CLAIMS" in load_prompt("verify").text
    assert "<<<RESEARCH" in load_prompt("brief").text
    assert "SUMMARY:" in load_prompt("quick").text


def test_register_jobs_and_no_schedule() -> None:
    registry = JobRegistry()
    register_jobs(registry)
    job_type = registry.get(RESEARCH_RUN)
    assert job_type is not None
    assert job_type.step_names == STEP_NAMES
    with pytest.raises(ValueError, match="already registered"):
        register_jobs(registry)
    entries = schedule_entries(CadenceSettings.default(SAMPLE_NOW))
    assert entries == []
    assert RESEARCH_RUN not in SCHEDULED_JOB_TYPES
