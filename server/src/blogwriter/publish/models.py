"""Request and response models of the publish routes (``domain.api.ApiModel``: camelCase,
unknown input refused)."""

from __future__ import annotations

from pydantic import AwareDatetime, Field

from blogwriter.domain.api import ApiModel, Versioned
from blogwriter.domain.articles import Article, PublishState
from blogwriter.domain.base import Slug
from blogwriter.domain.states import ArticleStatus

__all__ = [
    "ApprovalOut",
    "ApproveIn",
    "PublishJobView",
    "PublishNowIn",
    "PublishStatusOut",
    "PublishStepView",
]


class ApproveIn(ApiModel):
    """``POST /api/articles/{id}/approve``.

    ``publishAt`` defaults to the cadence's next article publish time (Monday 09:00 local).
    ``correctionNote`` is required for a live article (a correction) and becomes a dated note
    on the published page. ``slug`` confirms the slug: it must equal the article's; renaming a
    published slug (which leaves a redirect stub at the old one) requires it.
    """

    publish_at: AwareDatetime | None = None
    correction_note: str | None = Field(default=None, min_length=1, max_length=1000)
    slug: Slug | None = None


class PublishNowIn(ApiModel):
    """``POST /api/articles/{id}/publish-now`` (optional body): as approve, at once."""

    correction_note: str | None = Field(default=None, min_length=1, max_length=1000)
    slug: Slug | None = None


class ApprovalOut(ApiModel):
    article: Versioned[Article]
    job_id: str


class PublishStepView(ApiModel):
    name: str
    status: str


class PublishJobView(ApiModel):
    id: str
    type: str
    status: str
    steps: list[PublishStepView]
    error_code: str | None = None
    awaiting: str | None = None
    """The key the job waits for (``portfolio`` while publishing is not configured)."""


class PublishStatusOut(ApiModel):
    """``GET /api/articles/{id}/publish-status``."""

    status: ArticleStatus
    publish: PublishState
    last_error: str | None = None
    job: PublishJobView | None = None
