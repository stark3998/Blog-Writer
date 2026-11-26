"""RFC 9457 problem details (``application/problem+json``) for every error response.

Clients never see stack traces or exception messages. A problem body looks like::

    {"type": "about:blank", "title": "Forbidden", "status": 403,
     "code": "csrf_failed", "detail": "...", "request_id": "..."}

``code`` is a stable machine-readable reason (for example ``unauthenticated``,
``not_owner``, ``csrf_failed``, ``validation_failed``, ``internal_error``) and
``request_id`` matches the ``X-Request-Id`` response header and the log lines.

Routes raise :class:`ProblemError` (or FastAPI's ``HTTPException``) to return a problem.
Unexpected exceptions become a generic 500 problem through :class:`ErrorBoundaryMiddleware`,
which logs only the exception type and frames (see :func:`describe_exception`).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine, Mapping, Sequence
from http import HTTPStatus
from typing import Any, Final, cast

from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from blogwriter.core.logging import current_request_id, describe_exception, route_template

__all__ = [
    "PROBLEM_MEDIA_TYPE",
    "ErrorBoundaryMiddleware",
    "JsonValue",
    "ProblemError",
    "ProblemResponse",
    "exception_handlers",
    "problem_response",
]

PROBLEM_MEDIA_TYPE: Final = "application/problem+json"

type JsonValue = str | int | float | bool | list[JsonValue] | dict[str, JsonValue] | None

_RESERVED_MEMBERS: Final = frozenset({"type", "title", "status", "detail", "code", "request_id"})
_logger: Final = logging.getLogger("blogwriter.core.errors")


class ProblemResponse(JSONResponse):
    media_type = PROBLEM_MEDIA_TYPE


def _title(status: int) -> str:
    try:
        return HTTPStatus(status).phrase
    except ValueError:
        return "Error"


def problem_response(
    status: int,
    *,
    code: str | None = None,
    detail: str | None = None,
    title: str | None = None,
    headers: Mapping[str, str] | None = None,
    extensions: Mapping[str, JsonValue] | None = None,
) -> ProblemResponse:
    """Build a problem+json response. ``extensions`` may not reuse the standard members."""
    body: dict[str, JsonValue] = {
        "type": "about:blank",
        "title": title or _title(status),
        "status": status,
    }
    if detail:
        body["detail"] = detail
    if code:
        body["code"] = code
    request_id = current_request_id()
    if request_id is not None:
        body["request_id"] = request_id
    if extensions:
        clash = _RESERVED_MEMBERS.intersection(extensions)
        if clash:
            raise ValueError(f"extension members clash with standard members: {sorted(clash)}")
        body.update(extensions)
    return ProblemResponse(body, status_code=status, headers=dict(headers) if headers else None)


class ProblemError(Exception):
    """Raise from a route to return a problem+json response."""

    def __init__(
        self,
        status: int,
        *,
        code: str | None = None,
        detail: str | None = None,
        title: str | None = None,
        headers: Mapping[str, str] | None = None,
        extensions: Mapping[str, JsonValue] | None = None,
    ) -> None:
        super().__init__(code or _title(status))
        self.status = status
        self.code = code
        self.detail = detail
        self.title = title
        self.headers = dict(headers) if headers else None
        self.extensions = dict(extensions) if extensions else None

    def to_response(self) -> ProblemResponse:
        return problem_response(
            self.status,
            code=self.code,
            detail=self.detail,
            title=self.title,
            headers=self.headers,
            extensions=self.extensions,
        )


async def _problem_error_handler(request: Request, exc: Exception) -> Response:
    if not isinstance(exc, ProblemError):  # pragma: no cover - registered for ProblemError
        raise exc
    return exc.to_response()


async def _http_exception_handler(request: Request, exc: Exception) -> Response:
    if not isinstance(exc, StarletteHTTPException):  # pragma: no cover - registered for it
        raise exc
    if exc.status_code in (204, 304):
        return Response(status_code=exc.status_code, headers=exc.headers)
    title = _title(exc.status_code)
    detail = exc.detail if exc.detail != title else None
    return problem_response(exc.status_code, detail=detail, title=title, headers=exc.headers)


def _clean_validation_errors(errors: Sequence[Any]) -> list[JsonValue]:
    cleaned: list[JsonValue] = []
    for raw in errors:
        if not isinstance(raw, Mapping):
            continue
        error = cast(Mapping[str, object], raw)
        location = error.get("loc", ())
        loc: list[JsonValue] = []
        if isinstance(location, Sequence) and not isinstance(location, str):
            for part in cast(Sequence[object], location):
                loc.append(part if isinstance(part, str | int) else str(type(part).__name__))
        msg = error.get("msg")
        kind = error.get("type")
        # ``input`` and ``ctx`` are dropped: they echo request content back.
        cleaned.append(
            {
                "loc": loc,
                "msg": msg if isinstance(msg, str) else "invalid",
                "type": kind if isinstance(kind, str) else "invalid",
            }
        )
    return cleaned


async def _validation_error_handler(request: Request, exc: Exception) -> Response:
    if not isinstance(exc, RequestValidationError):  # pragma: no cover - registered for it
        raise exc
    return problem_response(
        422,
        code="validation_failed",
        extensions={"errors": _clean_validation_errors(exc.errors())},
    )


async def _internal_error_handler(request: Request, exc: Exception) -> Response:
    # Used by Starlette's outermost ServerErrorMiddleware, which re-raises afterwards so the
    # server logs the error (through the JSON formatter, without its message).
    return problem_response(500, code="internal_error")


type ExceptionHandlerFunc = Callable[[Request, Any], Coroutine[Any, Any, Response]]


def exception_handlers() -> dict[int | type[Exception], ExceptionHandlerFunc]:
    """Handlers to pass as ``FastAPI(exception_handlers=...)``."""
    return {
        ProblemError: _problem_error_handler,
        StarletteHTTPException: _http_exception_handler,
        RequestValidationError: _validation_error_handler,
        Exception: _internal_error_handler,
    }


class ErrorBoundaryMiddleware:
    """Turns unhandled exceptions into a logged, generic 500 problem.

    It sits inside the security-headers and access-log middlewares, so the 500 still gets
    the security headers and an access-log line. If the response had already started
    (a stream failed midway), the exception is logged and re-raised so the server closes
    the connection.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started: list[bool] = []  # non-empty once the response has started

        async def send_and_track(message: Message) -> None:
            if message["type"] == "http.response.start":
                started.append(True)
            await send(message)

        try:
            await self.app(scope, receive, send_and_track)
        except Exception as exc:
            response_started = bool(started)
            _logger.error(
                "unhandled_exception",
                extra={
                    "route": route_template(scope),
                    "aborted": response_started,
                    **describe_exception(exc),
                },
            )
            if response_started:
                raise
            await problem_response(500, code="internal_error")(scope, receive, send)
