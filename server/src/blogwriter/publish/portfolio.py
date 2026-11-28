"""The portfolio's GitHub Actions API: dispatch the build, find its run, read its status.

A configured API (security rule 7): its own ``httpx.AsyncClient`` with the fixed base URL
``https://api.github.com``, timeouts, no redirects, no proxies or environment settings
(``trust_env=False``). The PAT travels only in the ``Authorization`` header and is never
logged, stored or put in an error.

Correlation: the portfolio workflow declares a ``workflow_dispatch`` input ``correlation_id``
and a ``run-name`` that contains it (``run-name: Publish ${{ inputs.correlation_id }}``).
:meth:`PortfolioClient.dispatch` sends the id, :meth:`PortfolioClient.find_run` finds the run
whose title carries it as a whole token, so the publish job can poll exactly its own build.

Errors are :class:`PortfolioError` with a stable ``code`` and ``transient`` (worth retrying:
timeouts, network errors, rate limits, 5xx) or not (401, 403, 404, 422: configuration).
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from types import TracebackType
from typing import TYPE_CHECKING, Final, Self
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

from blogwriter.core.logging import register_log_fields

if TYPE_CHECKING:
    from blogwriter.publish.settings import PublishSettings

__all__ = [
    "API_BASE_URL",
    "API_VERSION",
    "RUN_STATUSES_DONE",
    "PortfolioClient",
    "PortfolioError",
    "correlation_id",
]

API_BASE_URL: Final = "https://api.github.com"
API_VERSION: Final = "2022-11-28"
USER_AGENT: Final = "BlogWriter/2.0"
DEFAULT_TIMEOUT_SECONDS: Final = 20.0
RUN_STATUSES_DONE: Final = frozenset({"completed"})
_RUNS_PAGE: Final = 50
_MAX_TITLE_CHARS: Final = 1024

_logger: Final = logging.getLogger("blogwriter.publish.portfolio")
register_log_fields("http_status", "portfolio_call", "portfolio_code")


class PortfolioError(Exception):
    """A GitHub call failed. ``code``: ``not_configured``, ``unauthorized``, ``forbidden``,
    ``not_found``, ``rejected`` (422), ``rate_limited``, ``unavailable`` (5xx), ``timeout``,
    ``unreachable``, ``bad_response``, ``unexpected_status``. The message is the code."""

    def __init__(self, code: str, *, transient: bool, status: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.transient = transient
        self.status = status


def correlation_id(job_id: str, salt: str = "") -> str:
    """``bw-<24 hex>``: one id per job (and per re-dispatch ``salt``), safe in a run name."""
    digest = hashlib.sha256(f"{job_id}\x1f{salt}".encode()).hexdigest()
    return f"bw-{digest[:24]}"


class _Run(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int = Field(ge=1)
    status: str | None = None
    conclusion: str | None = None
    name: str | None = None
    display_title: str | None = None


class _Runs(BaseModel):
    model_config = ConfigDict(extra="ignore")

    workflow_runs: list[_Run] = Field(default_factory=list[_Run])


def _mentions(title: str | None, token: str) -> bool:
    if not title:
        return False
    pattern = rf"(?<![A-Za-z0-9_-]){re.escape(token)}(?![A-Za-z0-9_-])"
    return re.search(pattern, title[:_MAX_TITLE_CHARS]) is not None


class PortfolioClient:
    """The three GitHub Actions calls the publish job makes. Use it as an async context manager
    (or call :meth:`aclose`)."""

    def __init__(
        self,
        repository: str,
        token: SecretStr,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        owner, _, name = repository.partition("/")
        if not owner or not name:
            raise ValueError("repository must look like owner/repo")
        self._repo_path = f"/repos/{quote(owner, safe='')}/{quote(name, safe='')}"
        self._client = httpx.AsyncClient(
            base_url=API_BASE_URL,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token.get_secret_value()}",
                "X-GitHub-Api-Version": API_VERSION,
                "User-Agent": USER_AGENT,
            },
            timeout=httpx.Timeout(timeout, connect=min(10.0, timeout)),
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )

    @classmethod
    def from_settings(
        cls, settings: PublishSettings, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> PortfolioClient:
        """A client for ``BW_PUBLISH_PORTFOLIO_*``; ``PortfolioError("not_configured")``
        without a repository or token."""
        if settings.portfolio_repository is None or settings.portfolio_token is None:
            raise PortfolioError("not_configured", transient=False)
        return cls(settings.portfolio_repository, settings.portfolio_token, transport=transport)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    # ----------------------------------------------------------------------------------------

    def _workflow_path(self, workflow: str) -> str:
        return f"{self._repo_path}/actions/workflows/{quote(workflow, safe='')}"

    async def _call(
        self,
        call: str,
        method: str,
        path: str,
        *,
        json: dict[str, object] | None = None,
        params: dict[str, str | int] | None = None,
    ) -> httpx.Response:
        started = time.monotonic()
        try:
            response = await self._client.request(method, path, json=json, params=params)
        except httpx.TimeoutException:
            raise self._failed(call, PortfolioError("timeout", transient=True), started) from None
        except httpx.HTTPError:
            error = PortfolioError("unreachable", transient=True)
            raise self._failed(call, error, started) from None
        if response.is_success:
            return response
        raise self._failed(call, _status_error(response), started)

    def _failed(self, call: str, error: PortfolioError, started: float) -> PortfolioError:
        _logger.warning(
            "portfolio_call_failed",
            extra={
                "portfolio_call": call,
                "portfolio_code": error.code,
                "http_status": error.status,
                "duration_ms": round((time.monotonic() - started) * 1000),
            },
        )
        return error

    async def dispatch(self, workflow: str, *, ref: str, correlation_id: str) -> None:
        """``workflow_dispatch`` the build with ``inputs.correlation_id`` (GitHub: 204)."""
        body: dict[str, object] = {"ref": ref, "inputs": {"correlation_id": correlation_id}}
        await self._call(
            "dispatch", "POST", f"{self._workflow_path(workflow)}/dispatches", json=body
        )

    async def find_run(self, workflow: str, correlation_id: str) -> int | None:
        """The newest ``workflow_dispatch`` run of ``workflow`` whose run name (or name) holds
        ``correlation_id`` as a whole token, or ``None`` while GitHub has not created it."""
        response = await self._call(
            "find_run",
            "GET",
            f"{self._workflow_path(workflow)}/runs",
            params={"event": "workflow_dispatch", "per_page": _RUNS_PAGE},
        )
        try:
            runs = _Runs.model_validate_json(response.content)
        except ValidationError:
            raise PortfolioError("bad_response", transient=True) from None
        matching = [
            run.id
            for run in runs.workflow_runs
            if _mentions(run.display_title, correlation_id) or _mentions(run.name, correlation_id)
        ]
        return max(matching) if matching else None

    async def run_status(self, run_id: int) -> tuple[str, str | None]:
        """``(status, conclusion)`` of a run: ``completed`` + ``success``/``failure``/...;
        anything else while it runs."""
        if run_id < 1:
            raise ValueError("run ids are positive")
        response = await self._call("run_status", "GET", f"{self._repo_path}/actions/runs/{run_id}")
        try:
            run = _Run.model_validate_json(response.content)
        except ValidationError:
            raise PortfolioError("bad_response", transient=True) from None
        if run.status is None:
            raise PortfolioError("bad_response", transient=True)
        return run.status, run.conclusion


def _status_error(response: httpx.Response) -> PortfolioError:
    status = response.status_code
    if status == 401:
        return PortfolioError("unauthorized", transient=False, status=status)
    if status == 403:
        limited = response.headers.get("x-ratelimit-remaining") == "0" or (
            "retry-after" in response.headers
        )
        if limited:
            return PortfolioError("rate_limited", transient=True, status=status)
        return PortfolioError("forbidden", transient=False, status=status)
    if status == 404:
        return PortfolioError("not_found", transient=False, status=status)
    if status == 422:
        return PortfolioError("rejected", transient=False, status=status)
    if status == 429:
        return PortfolioError("rate_limited", transient=True, status=status)
    if status >= 500:
        return PortfolioError("unavailable", transient=True, status=status)
    return PortfolioError("unexpected_status", transient=False, status=status)
