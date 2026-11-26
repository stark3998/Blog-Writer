"""Tests for blogwriter.core.logging: allowlisted JSON logs, request ids and the access log."""

from __future__ import annotations

import asyncio
import gc
import io
import json
import logging
import re
import sys
from collections.abc import Iterator
from typing import cast, override

import pytest
from fastapi import APIRouter

from blogwriter.core.app import create_app
from blogwriter.core.logging import (
    REQUEST_ID_HEADER,
    JsonFormatter,
    allowed_log_fields,
    configure_logging,
    register_log_fields,
)
from blogwriter.core.testing import (
    OTHER_OID,
    OWNER_OID,
    asgi_client,
    make_settings,
    principal_headers,
)

_logger = logging.getLogger("tests.core.logging")


class Capture(logging.Handler):
    """Formats records with the production JSON formatter and keeps the raw lines."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.setFormatter(JsonFormatter())
        self.lines: list[str] = []

    @override
    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(self.format(record))

    @property
    def text(self) -> str:
        return "\n".join(self.lines)

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


@pytest.fixture
def info_level() -> Iterator[None]:
    logger = logging.getLogger("tests.core")
    previous = logger.level
    logger.setLevel(logging.DEBUG)
    try:
        yield
    finally:
        logger.setLevel(previous)


def echo_router() -> APIRouter:
    router = APIRouter()

    async def echo(item_id: str) -> dict[str, int]:
        _logger.info("echo_called", extra={"count": len(item_id)})
        return {"length": len(item_id)}

    router.add_api_route("/api/echo/{item_id}", echo, methods=["GET"])
    return router


# ----------------------------------------------------------------------------------------------
# Formatter
# ----------------------------------------------------------------------------------------------


@pytest.mark.usefixtures("info_level")
def test_line_is_json_with_the_envelope(capture: Capture) -> None:
    _logger.info("job_claimed", extra={"status": 200, "route": "/api/jobs/{job_id}"})
    (line,) = capture.events("job_claimed")
    assert set(line) == {"ts", "level", "logger", "event", "status", "route"}
    assert line["level"] == "INFO"
    assert line["logger"] == "tests.core.logging"
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z", str(line["ts"]))


@pytest.mark.usefixtures("info_level")
def test_fields_outside_the_allowlist_are_dropped(capture: Capture) -> None:
    _logger.info(
        "model_call",
        extra={"status": 200, "prompt": "SECRET-PROMPT", "authorization": "Bearer SECRET-TOKEN"},
    )
    (line,) = capture.events("model_call")
    assert line["status"] == 200
    assert "prompt" not in line
    assert "authorization" not in line
    assert line["dropped_fields"] == ["authorization", "prompt"]
    assert "SECRET" not in capture.text


@pytest.mark.usefixtures("info_level")
def test_message_arguments_are_never_interpolated(capture: Capture) -> None:
    _logger.info("fetched %s with token=%s", "https://x.test/?q=SECRET-QUERY", "SECRET-TOKEN")
    (line,) = capture.events("fetched %s with token=%s")
    assert "SECRET" not in capture.text
    assert line["event"] == "fetched %s with token=%s"


@pytest.mark.usefixtures("info_level")
def test_exceptions_are_logged_by_type_and_frames_only(capture: Capture) -> None:
    def fail() -> None:
        raise ValueError("SECRET-MESSAGE with completion text")

    try:
        fail()
    except ValueError:
        _logger.exception("step_failed")
    (line,) = capture.events("step_failed")
    assert line["error_type"] == "ValueError"
    frames = line["error_frames"]
    assert isinstance(frames, list)
    labels = [str(frame) for frame in cast(list[object], frames)]
    assert labels
    assert all(re.fullmatch(r"[^:]+:\d+:\w+", label) for label in labels)
    assert labels[-1].endswith(":fail")
    assert "SECRET" not in capture.text
    assert "Traceback" not in capture.text


@pytest.mark.usefixtures("info_level")
def test_values_are_bounded_and_never_stringified(capture: Capture) -> None:
    class Opaque:
        @override
        def __str__(self) -> str:
            return "SECRET-STR"

    _logger.info(
        "values",
        extra={
            "count": float("nan"),
            "reason": "x" * 1000,
            "sha": Opaque(),
            "lag_ms": list(range(50)),
            "status": None,
        },
    )
    (line,) = capture.events("values")
    assert line["count"] == "nan"
    assert line["reason"] == "x" * 256
    assert line["sha"] == "<Opaque>"
    assert line["lag_ms"] == list(range(20))
    assert line["status"] is None
    assert "SECRET" not in capture.text


@pytest.mark.usefixtures("info_level")
def test_long_events_are_truncated(capture: Capture) -> None:
    _logger.info("e" * 500)
    assert json.loads(capture.lines[-1])["event"] == "e" * 200


@pytest.mark.usefixtures("info_level")
def test_registered_fields_are_allowed(capture: Capture) -> None:
    _logger.info("before", extra={"core_test_story_count": 3})
    register_log_fields("core_test_story_count")
    _logger.info("after", extra={"core_test_story_count": 3})
    assert "core_test_story_count" in allowed_log_fields()
    assert "core_test_story_count" not in capture.events("before")[0]
    assert capture.events("after")[0]["core_test_story_count"] == 3


@pytest.mark.parametrize("name", ["Bad", "x-y", "msg", "exc_info", "", "a" * 65])
def test_invalid_field_names_are_refused(name: str) -> None:
    with pytest.raises(ValueError, match="invalid log field name"):
        register_log_fields(name)


# ----------------------------------------------------------------------------------------------
# asyncio's pre-formatted messages
# ----------------------------------------------------------------------------------------------


def raised(error: BaseException) -> BaseException:
    try:
        raise error
    except BaseException as caught:
        return caught


@pytest.mark.parametrize(
    ("message", "event"),
    [
        (
            "Task exception was never retrieved\nfuture: <Task finished name='Task-7' "
            "coro=<run_job() done> exception=ValueError('SECRET-DRAFT-TEXT')>",
            "Task exception was never retrieved",
        ),
        (
            "Exception in callback deliver('SECRET-ARGUMENT')\nhandle: <Handle deliver('SECRET')>",
            "Exception in callback deliver",
        ),
        (
            "Task was destroyed but it is pending!\ntask: <Task pending coro=<f('SECRET')>>",
            "Task was destroyed but it is pending",
        ),
        ("<Task pending SECRET-REPR> came first", "asyncio_error"),
        (
            "Unclosed client session\nclient_session: <ClientSession SECRET-URL>",
            "Unclosed client session",
        ),
    ],
    ids=["task_exception", "callback", "destroyed", "repr_first", "unclosed_session"],
)
def test_asyncio_messages_keep_only_their_leading_words(
    capture: Capture, message: str, event: str
) -> None:
    error = raised(ValueError("SECRET-EXCEPTION-MESSAGE"))
    logging.getLogger("asyncio").error(message, exc_info=(type(error), error, error.__traceback__))
    (line,) = capture.events(event)
    assert line["logger"] == "asyncio"
    assert line["error_type"] == "ValueError"
    assert "SECRET" not in capture.text


def test_asyncio_templates_with_arguments_are_kept(capture: Capture) -> None:
    # Arguments are never interpolated, so the template itself is safe and more useful.
    logging.getLogger("asyncio").warning(
        "Executing %s took %.3f seconds", "<Handle deliver('SECRET')>", 0.7
    )
    assert capture.events("Executing %s took %.3f seconds")
    assert "SECRET" not in capture.text


def test_only_the_asyncio_logger_tree_is_trimmed(capture: Capture) -> None:
    logging.getLogger("asyncio.child").error("Child message: <x SECRET>")
    logging.getLogger("asyncioish").error("Other library: 1/2")
    assert capture.events("Child message")
    assert capture.events("Other library: 1/2")
    assert "SECRET" not in capture.text


def test_unretrieved_task_exception_does_not_leak_its_message(capture: Capture) -> None:
    async def fail() -> None:
        raise ValueError("SECRET-DRAFT-TEXT the owner's paragraph")

    async def main() -> None:
        task = asyncio.get_running_loop().create_task(fail())
        await asyncio.wait({task})  # waits without retrieving the exception
        del task  # never awaited: asyncio reports it when the task is collected
        gc.collect()

    asyncio.run(main())
    (line,) = capture.events("Task exception was never retrieved")
    assert line["level"] == "ERROR"
    assert line["error_type"] == "ValueError"
    assert "SECRET" not in capture.text


# ----------------------------------------------------------------------------------------------
# configure_logging
# ----------------------------------------------------------------------------------------------


def test_configure_logging_is_idempotent_and_turns_off_uvicorn_access() -> None:
    logging.getLogger("uvicorn.access").addHandler(logging.StreamHandler(io.StringIO()))
    logging.getLogger("uvicorn").addHandler(logging.StreamHandler(io.StringIO()))
    configure_logging()
    configure_logging()
    root = logging.getLogger()
    json_handlers = [h for h in root.handlers if getattr(h, "_blogwriter_json_handler", False)]
    assert len(json_handlers) == 1
    access = logging.getLogger("uvicorn.access")
    assert access.disabled
    assert access.handlers == []
    assert not access.propagate
    for name in ("uvicorn", "uvicorn.error"):
        assert logging.getLogger(name).handlers == []
        assert logging.getLogger(name).propagate
    for name in ("httpx", "httpcore", "azure", "openai", "anthropic"):
        assert logging.getLogger(name).level == logging.WARNING


def test_handler_writes_json_lines_to_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging()
    logging.getLogger("tests.core.stdout").warning("stdout_event", extra={"count": 2})
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]
    assert {"event": "stdout_event", "count": 2}.items() <= lines[-1].items()


class BrokenStream(io.StringIO):
    @override
    def write(self, s: str, /) -> int:
        raise OSError("stream closed")


def test_emit_failure_never_prints_the_record(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    configure_logging()
    monkeypatch.setattr(sys, "stdout", BrokenStream())
    logging.getLogger("tests.core.broken").warning("broken %s", "SECRET-ARG")
    err = capsys.readouterr().err
    assert '{"event":"log_emit_failed"}' in err
    assert "SECRET" not in err


# ----------------------------------------------------------------------------------------------
# Request id and access log
# ----------------------------------------------------------------------------------------------


async def test_logs_contain_no_query_string_path_values_or_headers(capture: Capture) -> None:
    app = create_app(make_settings(), routers=[echo_router()])
    secret_headers = {
        "Authorization": "Bearer SECRET-AUTH",
        "X-Api-Key": "SECRET-API-KEY",
        "Cookie": "session=SECRET-COOKIE",
        "User-Agent": "SECRET-USER-AGENT",
        "Referer": "https://writer.test/?SECRET-REFERER",
    }
    async with asgi_client(app) as client:
        ok = await client.get(
            "/api/echo/SECRET-ITEM-ID?token=SECRET-QUERY&code=SECRET-CODE",
            headers={**principal_headers(OWNER_OID, name="SECRET-NAME"), **secret_headers},
        )
        denied = await client.get(
            "/api/echo/SECRET-ITEM?token=SECRET-QUERY",
            headers={**principal_headers(OTHER_OID, name="SECRET-OTHER"), **secret_headers},
        )
        missing = await client.get(
            "/api/SECRET-UNKNOWN?token=SECRET-QUERY",
            headers={**principal_headers(OWNER_OID), **secret_headers},
        )
    assert (ok.status_code, denied.status_code, missing.status_code) == (200, 403, 404)
    assert "SECRET" not in capture.text

    access = capture.events("http_request")
    by_id = {line["request_id"]: line for line in access}
    ok_line = by_id[ok.headers[REQUEST_ID_HEADER]]
    assert ok_line == {
        "ts": ok_line["ts"],
        "level": "INFO",
        "logger": "blogwriter.core.access",
        "event": "http_request",
        "request_id": ok.headers[REQUEST_ID_HEADER],
        "method": "GET",
        "route": "/api/echo/{item_id}",
        "status": 200,
        "duration_ms": ok_line["duration_ms"],
    }
    assert isinstance(ok_line["duration_ms"], float)
    assert by_id[denied.headers[REQUEST_ID_HEADER]]["status"] == 403
    assert by_id[missing.headers[REQUEST_ID_HEADER]]["route"] is None

    (denial,) = capture.events("auth_denied")
    assert denial["oid"] == OTHER_OID
    assert denial["reason"] == "not_owner"
    (echo_line,) = capture.events("echo_called")
    assert echo_line["request_id"] == ok.headers[REQUEST_ID_HEADER]


async def test_route_template_includes_router_prefixes(capture: Capture) -> None:
    inner = APIRouter(prefix="/items")

    async def item(item_id: str) -> dict[str, str]:
        return {"item": item_id}

    inner.add_api_route("/{item_id}", item, methods=["GET"])
    outer = APIRouter(prefix="/api/nested")
    outer.include_router(inner, prefix="/v1")
    app = create_app(make_settings(), routers=[outer])
    async with asgi_client(app) as client:
        response = await client.get("/api/nested/v1/items/42", headers=principal_headers(OWNER_OID))
    assert response.status_code == 200
    (line,) = capture.events("http_request")
    assert line["route"] == "/api/nested/v1/items/{item_id}"


async def test_request_ids_are_fresh_and_not_taken_from_the_client() -> None:
    app = create_app(make_settings())
    async with asgi_client(app) as client:
        first = await client.get("/api/health", headers={REQUEST_ID_HEADER: "attacker-chosen"})
        second = await client.get("/api/health")
    ids = [first.headers[REQUEST_ID_HEADER], second.headers[REQUEST_ID_HEADER]]
    assert all(re.fullmatch(r"[0-9a-f]{32}", request_id) for request_id in ids)
    assert ids[0] != ids[1]


async def test_health_checks_log_at_debug(
    capture: Capture, caplog: pytest.LogCaptureFixture
) -> None:
    app = create_app(make_settings())
    with caplog.at_level(logging.DEBUG, logger="blogwriter.core.access"):
        async with asgi_client(app) as client:
            await client.get("/api/health")
            await client.get("/api/me")
    levels = {line["route"]: line["level"] for line in capture.events("http_request")}
    assert levels == {"/api/health": "DEBUG", None: "INFO"}
