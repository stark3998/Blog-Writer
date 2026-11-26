"""Tests for blogwriter.core.errors: problem+json everywhere, no stack traces to clients."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Iterator
from typing import override

import httpx
import pytest
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request
from starlette.responses import StreamingResponse

from blogwriter.core.app import create_app
from blogwriter.core.errors import (
    PROBLEM_MEDIA_TYPE,
    ProblemError,
    exception_handlers,
    problem_response,
)
from blogwriter.core.logging import REQUEST_ID_HEADER, JsonFormatter
from blogwriter.core.security_headers import API_CSP
from blogwriter.core.testing import (
    OWNER_OID,
    asgi_client,
    make_settings,
    principal_headers,
    write_headers,
)

OWNER = principal_headers(OWNER_OID)


class Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.setFormatter(JsonFormatter())
        self.lines: list[str] = []

    @override
    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(self.format(record))

    def events(self, event: str) -> list[dict[str, object]]:
        parsed: list[dict[str, object]] = [json.loads(line) for line in self.lines]
        return [line for line in parsed if line["event"] == event]


@pytest.fixture
def capture() -> Iterator[Capture]:
    handler = Capture()
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        yield handler
    finally:
        root.removeHandler(handler)


class Draft(BaseModel):
    title: str
    words: int


def error_router() -> APIRouter:
    router = APIRouter()

    async def boom() -> None:
        raise ValueError("SECRET-MESSAGE: the draft text")

    async def problem() -> None:
        raise ProblemError(
            409,
            code="version_conflict",
            detail="The draft changed since you opened it.",
            headers={"ETag": '"7"'},
            extensions={"current_etag": '"7"'},
        )

    async def http_error() -> None:
        raise HTTPException(status_code=404, detail="No such draft.")

    async def create(draft: Draft) -> dict[str, int]:
        return {"words": draft.words}

    async def stream() -> StreamingResponse:
        async def chunks() -> AsyncIterator[bytes]:
            yield b"data: one\n\n"
            raise RuntimeError("SECRET-STREAM-FAILURE")

        return StreamingResponse(chunks(), media_type="text/event-stream")

    router.add_api_route("/api/boom", boom, methods=["GET"])
    router.add_api_route("/api/problem", problem, methods=["GET"])
    router.add_api_route("/api/http-error", http_error, methods=["GET"])
    router.add_api_route("/api/drafts", create, methods=["POST"])
    router.add_api_route("/api/stream", stream, methods=["GET"])
    return router


def assert_problem(response: httpx.Response, status: int) -> dict[str, object]:
    assert response.status_code == status
    assert response.headers["content-type"] == PROBLEM_MEDIA_TYPE
    body: dict[str, object] = response.json()
    assert body["type"] == "about:blank"
    assert body["status"] == status
    assert body["request_id"] == response.headers[REQUEST_ID_HEADER]
    return body


async def test_unknown_api_route_is_a_404_problem() -> None:
    app = create_app(make_settings())
    async with asgi_client(app) as client:
        response = await client.get("/api/nothing-here", headers=OWNER)
    body = assert_problem(response, 404)
    assert body["title"] == "Not Found"
    assert "detail" not in body


async def test_wrong_method_is_a_405_problem_with_allow() -> None:
    settings = make_settings()
    app = create_app(settings)
    async with asgi_client(app) as client:
        response = await client.delete("/api/me", headers=OWNER)
    # DELETE is unsafe: without the app cookie it never reaches routing.
    assert_problem(response, 403)
    async with asgi_client(app) as client:
        response = await client.request("OPTIONS", "/api/me", headers=write_headers(settings))
    assert_problem(response, 405)
    assert response.headers["allow"] == "GET"


async def test_validation_errors_do_not_echo_the_input() -> None:
    settings = make_settings()
    app = create_app(settings, routers=[error_router()])
    async with asgi_client(app) as client:
        response = await client.post(
            "/api/drafts",
            json={"title": "SECRET-TITLE", "words": "SECRET-WORDS"},
            headers=write_headers(settings),
        )
    body = assert_problem(response, 422)
    assert body["code"] == "validation_failed"
    assert body["errors"] == [
        {
            "loc": ["body", "words"],
            "msg": "Input should be a valid integer, unable to parse string as an integer",
            "type": "int_parsing",
        }
    ]
    assert "SECRET" not in response.text


async def test_unhandled_exception_is_a_generic_500(capture: Capture) -> None:
    app = create_app(make_settings(), routers=[error_router()])
    async with asgi_client(app) as client:
        response = await client.get("/api/boom", headers=OWNER)
    body = assert_problem(response, 500)
    assert body == {
        "type": "about:blank",
        "title": "Internal Server Error",
        "status": 500,
        "code": "internal_error",
        "request_id": response.headers[REQUEST_ID_HEADER],
    }
    assert "SECRET" not in response.text
    assert "Traceback" not in response.text
    # The 500 still carries the security headers.
    assert response.headers["content-security-policy"] == API_CSP
    (logged,) = capture.events("unhandled_exception")
    assert logged["error_type"] == "ValueError"
    assert logged["route"] == "/api/boom"
    assert logged["aborted"] is False
    assert logged["request_id"] == response.headers[REQUEST_ID_HEADER]
    assert "SECRET" not in "\n".join(capture.lines)
    (access,) = capture.events("http_request")
    assert (access["status"], access["level"]) == (500, "ERROR")


async def test_failure_after_the_response_started_is_logged_and_reraised(
    capture: Capture,
) -> None:
    app = create_app(make_settings(), routers=[error_router()])
    async with asgi_client(app) as client:
        with pytest.raises(RuntimeError, match="SECRET-STREAM-FAILURE"):
            await client.get("/api/stream", headers=OWNER)
    (logged,) = capture.events("unhandled_exception")
    assert logged["aborted"] is True
    assert logged["error_type"] == "RuntimeError"
    assert "SECRET" not in "\n".join(capture.lines)


async def test_problem_error_from_a_route() -> None:
    app = create_app(make_settings(), routers=[error_router()])
    async with asgi_client(app) as client:
        response = await client.get("/api/problem", headers=OWNER)
    body = assert_problem(response, 409)
    assert body["code"] == "version_conflict"
    assert body["detail"] == "The draft changed since you opened it."
    assert body["current_etag"] == '"7"'
    assert response.headers["etag"] == '"7"'


async def test_http_exception_detail_is_kept() -> None:
    app = create_app(make_settings(), routers=[error_router()])
    async with asgi_client(app) as client:
        response = await client.get("/api/http-error", headers=OWNER)
    body = assert_problem(response, 404)
    assert body["detail"] == "No such draft."


def test_problem_response_refuses_to_override_standard_members() -> None:
    with pytest.raises(ValueError, match="clash"):
        problem_response(400, extensions={"status": 200})


def test_problem_response_outside_a_request_has_no_request_id() -> None:
    response = problem_response(418)
    body = json.loads(bytes(response.body))
    assert body == {"type": "about:blank", "title": "I'm a Teapot", "status": 418}
    assert response.media_type == PROBLEM_MEDIA_TYPE


def test_unknown_status_gets_a_generic_title() -> None:
    body = json.loads(bytes(problem_response(599).body))
    assert body["title"] == "Error"


async def test_outermost_handler_returns_a_generic_problem() -> None:
    handler = exception_handlers()[Exception]
    request = Request({"type": "http", "method": "GET", "path": "/", "headers": []})
    response = await handler(request, RuntimeError("SECRET"))
    assert response.status_code == 500
    assert b"SECRET" not in bytes(response.body)


async def test_not_modified_http_exception_has_no_body() -> None:
    handler = exception_handlers()[StarletteHTTPException]
    request = Request({"type": "http", "method": "GET", "path": "/", "headers": []})
    response = await handler(request, HTTPException(status_code=304))
    assert response.status_code == 304
    assert bytes(response.body) == b""
