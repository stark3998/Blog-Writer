"""Structured JSON logging with a field allowlist, plus the request-id and access-log middlewares.

Log lines are one JSON object each, written to stdout::

    {"ts": "...", "level": "INFO", "logger": "blogwriter.core.access", "event": "http_request",
     "request_id": "...", "method": "GET", "route": "/api/jobs/{job_id}", "status": 200,
     "duration_ms": 3.1}

Rules (see ``ARCHITECTURE.md``, rule 4):

* The message is logged as ``event`` **without** interpolating its arguments, so values
  passed as ``%s`` arguments never reach a log line. Use a constant ``snake_case`` event name
  and pass values as ``extra`` fields.
* Only allowlisted ``extra`` fields are written (:func:`allowed_log_fields`). Other fields are
  dropped and only their *names* are listed under ``dropped_fields``. Packages register the
  fields they need with :func:`register_log_fields` at import time.
* Exceptions are logged as ``error_type`` plus ``error_frames`` (``file:line:function``), never
  with the exception message, which may carry content.
* ``asyncio`` pre-formats reprs (exception messages, callback arguments) into the message of its
  "Task exception was never retrieved" style lines; only their leading words are logged.
* The access log records method, route template, status, duration and request id. It never
  records the raw path, query string, headers, client address or bodies. Uvicorn's own access
  log is switched off by :func:`configure_logging`.
"""

from __future__ import annotations

import json
import logging
import math
import re
import sys
import time
import traceback
import uuid
from collections.abc import Iterable
from contextvars import ContextVar
from datetime import UTC, date, datetime
from enum import Enum
from itertools import islice
from pathlib import PurePath
from typing import Final, Literal, cast, override

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

__all__ = [
    "REQUEST_ID_HEADER",
    "AccessLogMiddleware",
    "JsonFormatter",
    "RequestIdMiddleware",
    "allowed_log_fields",
    "configure_logging",
    "current_request_id",
    "describe_exception",
    "register_log_fields",
    "route_template",
]

#: Response header that carries the request id.
REQUEST_ID_HEADER: Final = "X-Request-Id"

_request_id: ContextVar[str | None] = ContextVar("blogwriter_request_id", default=None)

_CORE_FIELDS: Final = frozenset(
    {
        "aborted",
        "count",
        "duration_ms",
        "env",
        "error_frames",
        "error_type",
        "lag_ms",
        "lifecycle",
        "method",
        "oid",
        "reason",
        "request_id",
        "route",
        "sha",
        "status",
        "threshold_ms",
    }
)
_registered_fields: set[str] = set()
_FIELD_NAME_RE: Final = re.compile(r"[a-z][a-z0-9_]{0,63}")

# Attributes every LogRecord has; anything else on a record came from ``extra=``.
_RECORD_ATTRS: Final = frozenset(
    {
        *logging.LogRecord("", 0, "", 0, "", None, None).__dict__,
        "message",
        "asctime",
        "taskName",
    }
)

_MAX_EVENT_CHARS: Final = 200
_MAX_STR_CHARS: Final = 256
_MAX_LIST_ITEMS: Final = 20
_MAX_FRAMES: Final = 20

# Third-party loggers that log URLs, headers or payloads at INFO/DEBUG.
_NOISY_LOGGERS: Final = ("anthropic", "azure", "httpcore", "httpx", "openai", "urllib3")
# asyncio's default exception handler (uvloop's too) logs a pre-formatted message: the context
# message plus reprs of the task, callback arguments and exception, i.e. exception messages
# and argument values. Only its leading words are kept; ``exc_info`` still gives the type and
# frames.
_ASYNCIO_LOGGER: Final = "asyncio"
_LEADING_WORDS_RE: Final = re.compile(r"[A-Za-z ]*")
_ASYNCIO_FALLBACK_EVENT: Final = "asyncio_error"
_HANDLER_MARK: Final = "_blogwriter_json_handler"
_HEALTH_PATH: Final = "/api/health"

type _Scalar = str | int | float | bool | None
type _LogValue = _Scalar | list[_Scalar]


def current_request_id() -> str | None:
    """The id of the request being handled in this context, if any."""
    return _request_id.get()


def register_log_fields(*names: str) -> None:
    """Allow additional ``extra`` field names in log lines (call at import time).

    Only register fields that hold ids, counts, durations, costs or fixed codes; never
    content, prompts, tokens or headers.
    """
    for name in names:
        if not _FIELD_NAME_RE.fullmatch(name) or name in _RECORD_ATTRS:
            raise ValueError(f"invalid log field name: {name!r}")
    _registered_fields.update(names)


def allowed_log_fields() -> frozenset[str]:
    """Every field name a log line may carry besides the fixed envelope."""
    return _CORE_FIELDS | _registered_fields


def _frame_label(frame: traceback.FrameSummary) -> str:
    parts = PurePath(frame.filename).parts
    return f"{'/'.join(parts[-2:])}:{frame.lineno}:{frame.name}"


def describe_exception(exc: BaseException) -> dict[str, _LogValue]:
    """``error_type`` and ``error_frames`` for an exception, without its message."""
    cls = type(exc)
    error_type = (
        cls.__qualname__ if cls.__module__ == "builtins" else f"{cls.__module__}.{cls.__qualname__}"
    )
    frames = traceback.extract_tb(exc.__traceback__)[-_MAX_FRAMES:]
    labels: list[_Scalar] = [_frame_label(frame) for frame in frames]
    return {"error_type": error_type, "error_frames": labels}


def _clean_scalar(value: object) -> _Scalar:
    match value:
        case None | bool() | int():
            return value
        case float():
            return value if math.isfinite(value) else str(value)
        case str():
            return value[:_MAX_STR_CHARS]
        case Enum():
            return _clean_scalar(value.value)
        case uuid.UUID():
            return str(value)
        case datetime() | date():
            return value.isoformat()
        case _:
            # Never str() an unknown object: its text may be content.
            return f"<{type(value).__name__}>"


def _clean_value(value: object) -> _LogValue:
    if isinstance(value, list | tuple | set | frozenset):
        items = cast(Iterable[object], value)
        return [_clean_scalar(item) for item in islice(items, _MAX_LIST_ITEMS)]
    return _clean_scalar(value)


def _event(record: logging.LogRecord) -> str:
    if not isinstance(record.msg, str):
        return f"<{type(record.msg).__name__}>"
    is_asyncio = record.name == _ASYNCIO_LOGGER or record.name.startswith(f"{_ASYNCIO_LOGGER}.")
    if is_asyncio and not record.args:
        # A pre-formatted message (no %-arguments): "Task exception was never retrieved\n
        # future: <Task ... exception=ValueError('...')>" keeps "Task exception was never
        # retrieved"; "Exception in callback f('...')" keeps "Exception in callback f".
        match = _LEADING_WORDS_RE.match(record.msg)
        words = match.group().strip() if match is not None else ""
        return words or _ASYNCIO_FALLBACK_EVENT
    return record.msg


class JsonFormatter(logging.Formatter):
    """Formats a record as one JSON line holding only allowlisted fields."""

    @override
    def format(self, record: logging.LogRecord) -> str:
        event = _event(record)
        line: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "event": event[:_MAX_EVENT_CHARS],
        }
        request_id = _request_id.get()
        if request_id is not None:
            line["request_id"] = request_id
        allowed = allowed_log_fields()
        dropped: list[str] = []
        for key, value in record.__dict__.items():
            if key in _RECORD_ATTRS or key.startswith("_"):
                continue
            if key in allowed:
                line[key] = _clean_value(value)
            else:
                dropped.append(key)
        if record.exc_info is not None and record.exc_info[1] is not None:
            line.update(describe_exception(record.exc_info[1]))
        if dropped:
            line["dropped_fields"] = sorted(dropped)[:_MAX_LIST_ITEMS]
        return json.dumps(line, separators=(",", ":"))


class _StreamHandler(logging.Handler):
    """Writes to the *current* ``sys.stdout``/``sys.stderr`` (test harnesses swap them)."""

    def __init__(self, stream: Literal["stdout", "stderr"] = "stdout") -> None:
        super().__init__()
        self._stream_name: Literal["stdout", "stderr"] = stream

    @override
    def emit(self, record: logging.LogRecord) -> None:
        try:
            text = self.format(record)
            stream = sys.stdout if self._stream_name == "stdout" else sys.stderr
            stream.write(text + "\n")
            stream.flush()
        except Exception:
            self.handleError(record)

    @override
    def handleError(self, record: logging.LogRecord) -> None:
        # The stdlib version prints the record's message and arguments; never do that.
        try:
            sys.stderr.write('{"event":"log_emit_failed"}\n')
        except Exception:  # pragma: no cover - nothing left to report to
            return


def configure_logging(level: int = logging.INFO) -> None:
    """Route all logging through one JSON handler on stdout. Idempotent.

    * Adds the JSON handler to the root logger once (other root handlers, such as pytest's
      capture handler, are left alone).
    * Sends ``uvicorn``/``uvicorn.error`` through the root logger and switches
      ``uvicorn.access`` off; :class:`AccessLogMiddleware` replaces it.
    * Raises chatty SDK loggers that log URLs or headers to ``WARNING``.
    """
    root = logging.getLogger()
    if not any(getattr(handler, _HANDLER_MARK, False) for handler in root.handlers):
        handler = _StreamHandler()
        handler.setFormatter(JsonFormatter())
        setattr(handler, _HANDLER_MARK, True)
        root.addHandler(handler)
    root.setLevel(level)
    for name in ("uvicorn", "uvicorn.error"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True
    access = logging.getLogger("uvicorn.access")
    access.handlers.clear()
    access.propagate = False
    access.disabled = True
    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


def route_template(scope: Scope) -> str | None:
    """The matched route's path template (for example ``/api/jobs/{job_id}``), if any.

    FastAPI 0.165 no longer copies included routes, so ``scope["route"]`` holds the route
    as declared inside its router (without an ``include_router(prefix=...)`` prefix). The
    effective route context FastAPI records in ``scope["fastapi"]`` has the full template;
    it is preferred and the declared route is the fallback.
    """
    fastapi_scope: object = scope.get("fastapi")
    if isinstance(fastapi_scope, dict):
        context = cast(dict[str, object], fastapi_scope).get("effective_route_context")
        path_format = getattr(context, "path_format", None)
        if isinstance(path_format, str) and path_format:
            return path_format
    route: object = scope.get("route")
    for attribute in ("path_format", "path"):
        value = getattr(route, attribute, None)
        if isinstance(value, str) and value:
            return value
    return None


class RequestIdMiddleware:
    """Gives every request a fresh id (context variable + ``X-Request-Id`` response header).

    Incoming ``X-Request-Id`` headers are ignored, so clients cannot choose or forge ids.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        request_id = uuid.uuid4().hex
        token = _request_id.set(request_id)

        async def send_with_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            _request_id.reset(token)


_access_logger: Final = logging.getLogger("blogwriter.core.access")
_METHOD_RE: Final = re.compile(r"[A-Z]{1,16}")


class AccessLogMiddleware:
    """Logs one ``http_request`` line per request: method, route, status, duration_ms.

    Successful health checks log at DEBUG so probes do not flood the log.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = time.perf_counter()
        statuses: list[int] = []  # filled by the send wrapper

        async def send_and_record(message: Message) -> None:
            if message["type"] == "http.response.start":
                statuses.append(int(message["status"]))
            await send(message)

        aborted = False
        try:
            await self.app(scope, receive, send_and_record)
        except BaseException:
            aborted = True
            raise
        finally:
            status = statuses[0] if statuses else None
            method = str(scope.get("method", ""))
            fields: dict[str, object] = {
                "method": method if _METHOD_RE.fullmatch(method) else "OTHER",
                "route": route_template(scope),
                "status": status,
                "duration_ms": round((time.perf_counter() - started) * 1000, 1),
            }
            if aborted:
                fields["aborted"] = True
            level = _access_level(status, aborted=aborted, path=str(scope.get("path", "")))
            _access_logger.log(level, "http_request", extra=fields)


def _access_level(status: int | None, *, aborted: bool, path: str) -> int:
    if aborted or status is None or status >= 500:
        return logging.ERROR
    if status == 200 and path == _HEALTH_PATH:
        return logging.DEBUG
    return logging.INFO
