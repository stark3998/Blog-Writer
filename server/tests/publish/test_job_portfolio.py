"""The portfolio's GitHub Actions client over ``httpx.MockTransport`` (no network): requests,
run lookup by correlation id, status, error mapping, and that the token never leaks."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable

import httpx
import pytest
from pydantic import SecretStr

from blogwriter.publish.portfolio import (
    API_VERSION,
    PortfolioClient,
    PortfolioError,
    correlation_id,
)
from blogwriter.publish.testing import TEST_TOKEN, make_publish_settings

type Handler = Callable[[httpx.Request], httpx.Response]


def client(handler: Handler) -> PortfolioClient:
    return PortfolioClient(
        "owner/site", SecretStr(TEST_TOKEN), transport=httpx.MockTransport(handler)
    )


async def test_dispatch_sends_the_correlation_id_with_the_token_in_the_header_only() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(204)

    async with client(handler) as github:
        await github.dispatch("deploy.yml", ref="main", correlation_id="bw-abc")
    request = seen[0]
    assert request.method == "POST"
    assert request.url.host == "api.github.com"
    assert request.url.path == "/repos/owner/site/actions/workflows/deploy.yml/dispatches"
    assert json.loads(request.content) == {"ref": "main", "inputs": {"correlation_id": "bw-abc"}}
    assert request.headers["Authorization"] == f"Bearer {TEST_TOKEN}"
    assert request.headers["X-GitHub-Api-Version"] == API_VERSION
    assert request.headers["Accept"] == "application/vnd.github+json"
    assert TEST_TOKEN not in str(request.url)
    assert TEST_TOKEN not in request.content.decode()


async def test_find_run_matches_the_whole_token_and_takes_the_newest() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["event"] == "workflow_dispatch"
        runs = [
            {"id": 7, "display_title": "Publish bw-abc-r1", "status": "queued"},
            {"id": 9, "display_title": "Publish bw-abc", "status": "queued"},
            {"id": 8, "display_title": "Publish bw-abc", "status": "completed"},
            {"id": 10, "name": "Deploy", "display_title": "Publish xbw-abc"},
        ]
        return httpx.Response(200, json={"total_count": 4, "workflow_runs": runs})

    async with client(handler) as github:
        assert await github.find_run("deploy.yml", "bw-abc") == 9
        assert await github.find_run("deploy.yml", "bw-abc-r1") == 7
        assert await github.find_run("deploy.yml", "bw-zzz") is None


async def test_run_status_reads_status_and_conclusion() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/repos/owner/site/actions/runs/42"
        return httpx.Response(200, json={"id": 42, "status": "completed", "conclusion": "failure"})

    async with client(handler) as github:
        assert await github.run_status(42) == ("completed", "failure")
        with pytest.raises(ValueError, match="positive"):
            await github.run_status(0)


@pytest.mark.parametrize(
    ("status", "headers", "code", "transient"),
    [
        (401, {}, "unauthorized", False),
        (403, {}, "forbidden", False),
        (403, {"x-ratelimit-remaining": "0"}, "rate_limited", True),
        (404, {}, "not_found", False),
        (422, {}, "rejected", False),
        (429, {}, "rate_limited", True),
        (502, {}, "unavailable", True),
        (302, {"location": "https://elsewhere.test/"}, "unexpected_status", False),
    ],
)
async def test_errors_map_to_stable_codes(
    status: int, headers: dict[str, str], code: str, transient: bool
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, headers=headers, json={"message": "nope"})

    async with client(handler) as github:
        with pytest.raises(PortfolioError) as caught:
            await github.dispatch("deploy.yml", ref="main", correlation_id="bw-1")
    assert caught.value.code == code
    assert caught.value.transient is transient
    assert caught.value.status == status


async def test_timeouts_transport_errors_and_bad_json_are_transient() -> None:
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    def broken(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    def garbage(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json")

    for handler, code in ((timeout, "timeout"), (broken, "unreachable"), (garbage, "bad_response")):
        async with client(handler) as github:
            with pytest.raises(PortfolioError) as caught:
                await github.find_run("deploy.yml", "bw-1")
        assert caught.value.code == code
        assert caught.value.transient


async def test_the_token_never_reaches_logs_or_errors(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "Bad credentials"})

    async with client(handler) as github:
        with pytest.raises(PortfolioError) as caught:
            await github.dispatch("deploy.yml", ref="main", correlation_id="bw-1")
    assert TEST_TOKEN not in str(caught.value)
    assert TEST_TOKEN not in repr(caught.value)
    for record in caplog.records:
        assert TEST_TOKEN not in record.getMessage()
        assert TEST_TOKEN not in json.dumps(record.__dict__, default=str)


def test_from_settings_needs_a_repository_and_a_token() -> None:
    with pytest.raises(PortfolioError) as caught:
        PortfolioClient.from_settings(make_publish_settings(portfolio_token=None))
    assert caught.value.code == "not_configured"
    assert not caught.value.transient
    with pytest.raises(ValueError, match="owner/repo"):
        PortfolioClient("no-slash", SecretStr(TEST_TOKEN))


def test_correlation_ids_are_stable_per_job_and_salt() -> None:
    first = correlation_id("publish-art-1-v00001")
    assert first == correlation_id("publish-art-1-v00001")
    assert first.startswith("bw-")
    assert len(first) == 27
    assert first != correlation_id("publish-art-1-v00001-r1")
    assert first != correlation_id("publish-art-1-v00001", "-1")
