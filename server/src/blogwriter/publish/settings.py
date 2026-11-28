"""Publish settings (environment prefix ``BW_PUBLISH_``): one class for the whole package.

``BW_PUBLISH_CONTRACTS_DIR`` (unset)
    The ``contracts/`` directory (the same variable ``contract.ContractSettings`` reads, so the
    validator, the sanitizer profile and these settings always agree). Unset: the nearest
    ``contracts/`` above the package.
``BW_PUBLISH_PORTFOLIO_REPOSITORY`` (unset)
    ``owner/repo`` of the portfolio site.
``BW_PUBLISH_PORTFOLIO_REF`` (``main``)
    The branch the build workflow is dispatched on.
``BW_PUBLISH_PORTFOLIO_WORKFLOW`` (unset)
    The build workflow's file name (``deploy.yml``) or numeric id. It must declare a
    ``correlation_id`` ``workflow_dispatch`` input and a ``run-name`` that contains it.
``BW_PUBLISH_PORTFOLIO_TOKEN`` (unset, secret)
    A fine-grained PAT with Actions read and write on that repository only. Never logged.
``BW_PUBLISH_SITE_ORIGIN`` (unset)
    ``https://example.com``: articles are verified at ``<origin>/blog/<slug>``.
``BW_PUBLISH_PREVIEW_WORKFLOW`` (unset)
    The staging preview workflow (builds from ``published-blogs-staging`` only).
``BW_PUBLISH_BUILD_TIMEOUT_SECONDS`` (1800), ``BW_PUBLISH_VERIFY_TIMEOUT_SECONDS`` (900),
``BW_PUBLISH_POLL_SECONDS`` (20)
    How long the publish job waits for the build and for the live page, and how often it asks.

Unset portfolio settings are safe: approval still works, a publish job stops in a waiting state
with an alert (it never fails silently), unpublish and staging previews answer 503
``portfolio_not_configured``.
"""

from __future__ import annotations

import functools
import re
from pathlib import Path
from typing import Final
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from blogwriter.domain.base import check_https_url

__all__ = [
    "ARTICLE_PATH",
    "PublishSettings",
    "publish_settings",
]

ARTICLE_PATH: Final = "/blog/"
"""Where the portfolio serves an article: ``<site origin>/blog/<slug>``."""

_REPOSITORY_RE: Final = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9._-]{1,100}")
_WORKFLOW_RE: Final = re.compile(r"[0-9]{1,20}|[A-Za-z0-9][A-Za-z0-9._-]{0,150}\.ya?ml")
_REF_RE: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,199}")
_TOKEN_RE: Final = re.compile(r"[\x21-\x7e]{20,255}")


class PublishSettings(BaseSettings):
    """Settings for ``blogwriter.publish`` (contract section 1.10). Defaults are safe."""

    model_config = SettingsConfigDict(
        env_prefix="BW_PUBLISH_",
        env_ignore_empty=True,
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
    )

    contracts_dir: Path | None = None
    portfolio_repository: str | None = None
    portfolio_ref: str = "main"
    portfolio_workflow: str | None = None
    portfolio_token: SecretStr | None = None
    site_origin: str | None = None
    preview_workflow: str | None = None
    build_timeout_seconds: float = Field(default=1800.0, gt=0, le=6 * 3600)
    verify_timeout_seconds: float = Field(default=900.0, gt=0, le=6 * 3600)
    poll_seconds: float = Field(default=20.0, gt=0, le=600)

    @field_validator("portfolio_repository")
    @classmethod
    def _repository(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        name = text.partition("/")[2]
        if not _REPOSITORY_RE.fullmatch(text) or name in (".", ".."):
            raise ValueError("the portfolio repository looks like owner/repo")
        return text

    @field_validator("portfolio_workflow", "preview_workflow")
    @classmethod
    def _workflow(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        if not _WORKFLOW_RE.fullmatch(text):
            raise ValueError("a workflow is its file name (deploy.yml) or its numeric id")
        return text

    @field_validator("portfolio_ref")
    @classmethod
    def _ref(cls, value: str) -> str:
        text = value.strip()
        if not _REF_RE.fullmatch(text) or ".." in text or text.endswith(("/", ".lock")):
            raise ValueError("the portfolio ref is a branch name such as main")
        return text

    @field_validator("portfolio_token")
    @classmethod
    def _token(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None:
            return None
        if not _TOKEN_RE.fullmatch(value.get_secret_value().strip()):
            raise ValueError("the portfolio token must be 20-255 printable characters")
        return SecretStr(value.get_secret_value().strip())

    @field_validator("site_origin")
    @classmethod
    def _origin(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = check_https_url(value.strip().rstrip("/"))
        parts = urlsplit(text)
        if parts.path or parts.query or parts.fragment:
            raise ValueError("the site origin is scheme and host only, e.g. https://example.com")
        host = (parts.hostname or "").rstrip(".").lower()
        port = parts.port
        return f"https://{host}" if port in (None, 443) else f"https://{host}:{port}"

    @property
    def portfolio_configured(self) -> bool:
        """Everything a publish or unpublish needs: repository, workflow, token, site origin."""
        return not self.missing_for_publish()

    @property
    def preview_configured(self) -> bool:
        """Everything a staging preview needs: repository, token, preview workflow and the
        site origin (the preview document carries its canonical URL)."""
        return (
            self.portfolio_repository is not None
            and self.portfolio_token is not None
            and self.preview_workflow is not None
            and self.site_origin is not None
        )

    def missing_for_publish(self) -> list[str]:
        """Names (never values) of the variables a publish still needs."""
        wanted = {
            "BW_PUBLISH_PORTFOLIO_REPOSITORY": self.portfolio_repository,
            "BW_PUBLISH_PORTFOLIO_WORKFLOW": self.portfolio_workflow,
            "BW_PUBLISH_PORTFOLIO_TOKEN": self.portfolio_token,
            "BW_PUBLISH_SITE_ORIGIN": self.site_origin,
        }
        return [name for name, value in wanted.items() if value is None]

    def article_url(self, slug: str) -> str:
        """``<site origin>/blog/<slug>`` (``ValueError`` without a site origin)."""
        if self.site_origin is None:
            raise ValueError("BW_PUBLISH_SITE_ORIGIN is not set")
        return f"{self.site_origin}{ARTICLE_PATH}{slug}"


@functools.cache
def publish_settings() -> PublishSettings:
    """The process-wide settings, read from the environment once (tests: ``cache_clear()``)."""
    return PublishSettings()
