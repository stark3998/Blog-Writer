"""Diagnostics Router — connection and capability checks for external integrations."""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any, Callable, Literal

import requests
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from backend.db.cosmos_client import list_drafts
from backend.services.blog_service import _get_openai_client
from backend.tools.blog_publisher import _headers as github_headers
from backend.tools.linkedin_publisher import get_connection_status

router = APIRouter(prefix="/api/diagnostics", tags=["diagnostics"])


DiagnosticStatus = Literal["pass", "fail", "warn", "skip"]


class DiagnosticsCheckFlags(BaseModel):
    linkedin: bool = True
    foundry_config: bool = True
    text_generation: bool = True
    image_generation: bool = True
    cosmos: bool = True
    publish_dry_run: bool = True


class DiagnosticsRunRequest(BaseModel):
    session_id: str = ""
    include_billable: bool = True
    checks: DiagnosticsCheckFlags = Field(default_factory=DiagnosticsCheckFlags)


class DiagnosticsCheckResult(BaseModel):
    key: str
    label: str
    status: DiagnosticStatus
    severity: Literal["info", "warning", "error"]
    billable: bool = False
    duration_ms: int
    recommendation: str = ""
    details: dict[str, Any] = Field(default_factory=dict)


class DiagnosticsSummary(BaseModel):
    total: int
    passed: int
    failed: int
    warned: int
    skipped: int


class DiagnosticsRunResponse(BaseModel):
    timestamp: str
    overall_status: Literal["healthy", "degraded", "unhealthy"]
    summary: DiagnosticsSummary
    checks: list[DiagnosticsCheckResult]


class DiagnosticsCheckMetadata(BaseModel):
    key: str
    label: str
    billable: bool
    description: str


class DiagnosticsChecksResponse(BaseModel):
    checks: list[DiagnosticsCheckMetadata]


def _require_diagnostics_key(x_diagnostics_key: str | None = Header(default=None, alias="X-Diagnostics-Key")) -> None:
    expected = os.environ.get("DIAGNOSTICS_API_KEY", "").strip()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="Diagnostics API key is not configured on server (DIAGNOSTICS_API_KEY).",
        )

    if x_diagnostics_key != expected:
        raise HTTPException(status_code=401, detail="Invalid diagnostics API key")


def _run_check(
    *,
    key: str,
    label: str,
    billable: bool,
    include_billable: bool,
    recommendation: str,
    fn: Callable[[], tuple[DiagnosticStatus, dict[str, Any], str | None]],
) -> DiagnosticsCheckResult:
    started = time.time()

    if billable and not include_billable:
        return DiagnosticsCheckResult(
            key=key,
            label=label,
            status="skip",
            severity="info",
            billable=True,
            duration_ms=0,
            recommendation="Enable billable checks to run this test.",
            details={"reason": "billable-disabled"},
        )

    try:
        status, details, override_recommendation = fn()
        duration_ms = int((time.time() - started) * 1000)
        severity: Literal["info", "warning", "error"] = "info"
        if status == "warn":
            severity = "warning"
        elif status == "fail":
            severity = "error"

        return DiagnosticsCheckResult(
            key=key,
            label=label,
            status=status,
            severity=severity,
            billable=billable,
            duration_ms=duration_ms,
            recommendation=override_recommendation or recommendation,
            details=details,
        )
    except Exception as exc:
        duration_ms = int((time.time() - started) * 1000)
        return DiagnosticsCheckResult(
            key=key,
            label=label,
            status="fail",
            severity="error",
            billable=billable,
            duration_ms=duration_ms,
            recommendation=recommendation,
            details={"error": str(exc), "error_type": type(exc).__name__},
        )


def _check_linkedin(session_id: str) -> tuple[DiagnosticStatus, dict[str, Any], str | None]:
    client_id = os.environ.get("LINKEDIN_CLIENT_ID", "").strip()
    redirect_uri = os.environ.get("LINKEDIN_REDIRECT_URI", "").strip()
    scopes = os.environ.get("LINKEDIN_SCOPES", "").strip()

    missing = [
        name
        for name, value in {
            "LINKEDIN_CLIENT_ID": client_id,
            "LINKEDIN_REDIRECT_URI": redirect_uri,
            "LINKEDIN_SCOPES": scopes,
        }.items()
        if not value
    ]

    details: dict[str, Any] = {
        "client_id_present": bool(client_id),
        "redirect_uri": redirect_uri,
        "scopes": scopes,
        "session_id": session_id,
    }

    if missing:
        details["missing_env"] = missing
        return "fail", details, "Configure missing LinkedIn OAuth environment variables."

    if "openid" not in scopes:
        return "warn", details, "Prefer OpenID scopes: openid profile w_member_social."

    if not session_id:
        return "warn", details, "Provide a LinkedIn session_id to validate active OAuth connection."

    status = get_connection_status(session_id)
    details["session"] = status

    if status.get("connected"):
        return "pass", details, None
    return "warn", details, "Reconnect LinkedIn OAuth for this session from the Diagnostics page."


def _check_foundry_config() -> tuple[DiagnosticStatus, dict[str, Any], str | None]:
    endpoint = os.environ.get("PROJECT_ENDPOINT", "").strip()
    api_version = os.environ.get("API_VERSION", "2024-12-01-preview").strip()
    model = os.environ.get("MODEL_DEPLOYMENT_NAME", "gpt-4o").strip()
    image_model = os.environ.get("IMAGE_MODEL_DEPLOYMENT_NAME", "gpt-image-1-mini").strip()

    details: dict[str, Any] = {
        "endpoint": endpoint,
        "api_version": api_version,
        "model": model,
        "image_model": image_model,
    }

    missing = [k for k, v in {"PROJECT_ENDPOINT": endpoint, "API_VERSION": api_version, "MODEL_DEPLOYMENT_NAME": model}.items() if not v]
    if missing:
        details["missing_env"] = missing
        return "fail", details, "Set PROJECT_ENDPOINT, API_VERSION, and MODEL_DEPLOYMENT_NAME."

    if not endpoint.startswith("https://"):
        return "warn", details, "PROJECT_ENDPOINT should be a valid https endpoint."

    return "pass", details, None


def _check_text_generation() -> tuple[DiagnosticStatus, dict[str, Any], str | None]:
    client, model = _get_openai_client()
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "Return exactly the text OK."},
            {"role": "user", "content": "healthcheck"},
        ],
        temperature=0,
        max_completion_tokens=16,
    )
    content = (response.choices[0].message.content or "").strip()
    usage = response.usage.total_tokens if response.usage else 0

    return "pass", {"model": model, "api_version": os.environ.get("API_VERSION", ""), "output": content, "tokens": usage}, None


def _check_image_generation() -> tuple[DiagnosticStatus, dict[str, Any], str | None]:
    client, _ = _get_openai_client()
    image_model = os.environ.get("IMAGE_MODEL_DEPLOYMENT_NAME", "gpt-image-1-mini")

    response = client.images.generate(
        model=image_model,
        prompt="Minimal abstract gradient square, no text.",
        n=1,
        size="1024x1024",
    )
    image_url = response.data[0].url if response.data else ""

    if image_url:
        return "pass", {"model": image_model, "image_url_preview": image_url[:120]}, None
    return "fail", {"model": image_model}, "Image generation returned no URL."


def _check_cosmos() -> tuple[DiagnosticStatus, dict[str, Any], str | None]:
    items = list_drafts(limit=1)
    return "pass", {"endpoint": os.environ.get("COSMOS_ENDPOINT", ""), "sample_count": len(items)}, None


def _check_publish_dry_run() -> tuple[DiagnosticStatus, dict[str, Any], str | None]:
    repo = os.environ.get("GITHUB_REPO", "").strip()
    if not repo:
        return "fail", {"repo": repo}, "Set GITHUB_REPO environment variable."

    headers = github_headers()
    response = requests.get(f"https://api.github.com/repos/{repo}", headers=headers, timeout=20)
    if response.status_code >= 400:
        return (
            "fail",
            {"repo": repo, "status_code": response.status_code, "response": response.text[:200]},
            "Fix GITHUB_TOKEN permissions (repo scope) or GITHUB_REPO value.",
        )

    data = response.json()
    return "pass", {"repo": repo, "default_branch": data.get("default_branch", "main")}, None


@router.get("/checks", response_model=DiagnosticsChecksResponse, dependencies=[Depends(_require_diagnostics_key)])
async def get_checks_metadata():
    return DiagnosticsChecksResponse(
        checks=[
            DiagnosticsCheckMetadata(key="linkedin", label="LinkedIn OAuth and Session", billable=False, description="Validates LinkedIn OAuth config and optional active session."),
            DiagnosticsCheckMetadata(key="foundry_config", label="Foundry/OpenAI Config", billable=False, description="Checks endpoint, deployment model, and API version settings."),
            DiagnosticsCheckMetadata(key="text_generation", label="Text Generation Smoke Test", billable=True, description="Runs a tiny completion call against configured text model."),
            DiagnosticsCheckMetadata(key="image_generation", label="Image Generation Smoke Test", billable=True, description="Runs a tiny image generation call against configured image model."),
            DiagnosticsCheckMetadata(key="cosmos", label="Cosmos Connectivity", billable=False, description="Performs a lightweight read operation from drafts container."),
            DiagnosticsCheckMetadata(key="publish_dry_run", label="Publish Dry Run", billable=False, description="Validates GitHub publishing prerequisites without publishing."),
        ]
    )


@router.post("/run", response_model=DiagnosticsRunResponse, dependencies=[Depends(_require_diagnostics_key)])
async def run_diagnostics(request: DiagnosticsRunRequest):
    results: list[DiagnosticsCheckResult] = []

    if request.checks.linkedin:
        results.append(
            _run_check(
                key="linkedin",
                label="LinkedIn OAuth and Session",
                billable=False,
                include_billable=request.include_billable,
                recommendation="Ensure redirect URI and scopes match your LinkedIn app configuration.",
                fn=lambda: _check_linkedin(request.session_id.strip()),
            )
        )

    if request.checks.foundry_config:
        results.append(
            _run_check(
                key="foundry_config",
                label="Foundry/OpenAI Config",
                billable=False,
                include_billable=request.include_billable,
                recommendation="Set Foundry endpoint, model deployment name, and API version.",
                fn=_check_foundry_config,
            )
        )

    if request.checks.text_generation:
        results.append(
            _run_check(
                key="text_generation",
                label="Text Generation Smoke Test",
                billable=True,
                include_billable=request.include_billable,
                recommendation="Verify model deployment exists and supports chat completions.",
                fn=_check_text_generation,
            )
        )

    if request.checks.image_generation:
        results.append(
            _run_check(
                key="image_generation",
                label="Image Generation Smoke Test",
                billable=True,
                include_billable=request.include_billable,
                recommendation="Verify IMAGE_MODEL_DEPLOYMENT_NAME points to a supported image model.",
                fn=_check_image_generation,
            )
        )

    if request.checks.cosmos:
        results.append(
            _run_check(
                key="cosmos",
                label="Cosmos Connectivity",
                billable=False,
                include_billable=request.include_billable,
                recommendation="Check COSMOS_ENDPOINT and COSMOS_KEY credentials and network access.",
                fn=_check_cosmos,
            )
        )

    if request.checks.publish_dry_run:
        results.append(
            _run_check(
                key="publish_dry_run",
                label="Publish Dry Run",
                billable=False,
                include_billable=request.include_billable,
                recommendation="Verify GITHUB_TOKEN repo scope and GITHUB_REPO value.",
                fn=_check_publish_dry_run,
            )
        )

    summary = DiagnosticsSummary(
        total=len(results),
        passed=len([r for r in results if r.status == "pass"]),
        failed=len([r for r in results if r.status == "fail"]),
        warned=len([r for r in results if r.status == "warn"]),
        skipped=len([r for r in results if r.status == "skip"]),
    )

    overall_status: Literal["healthy", "degraded", "unhealthy"] = "healthy"
    if summary.failed > 0:
        overall_status = "unhealthy"
    elif summary.warned > 0:
        overall_status = "degraded"

    return DiagnosticsRunResponse(
        timestamp=datetime.now(timezone.utc).isoformat(),
        overall_status=overall_status,
        summary=summary,
        checks=results,
    )
