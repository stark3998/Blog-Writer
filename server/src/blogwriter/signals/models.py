"""Request and response models of the signals routes (camelCase, unknown fields refused).

Clients never send whole documents: these models carry the owner-editable fields only.
"""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import AfterValidator, Field, StringConstraints, model_validator

from blogwriter.domain.api import ApiModel
from blogwriter.domain.base import HttpsUrl, Slug, Timestamp
from blogwriter.domain.posts import Lane
from blogwriter.domain.signals import FORMAT_ID_PATTERN, SourceType

__all__ = [
    "DiscoverIn",
    "DiscoverOut",
    "DiscoveredFeedOut",
    "IdeaEditIn",
    "IdeaIn",
    "IdeaLane",
    "IdeaTransitionIn",
    "SourceIn",
]

type IdeaLane = Lane | Literal["flagship"]
type PillarId = Annotated[Slug, Field(max_length=40)]


def _one_line(value: str) -> str:
    text = " ".join(value.split())
    if not text:
        raise ValueError("must not be blank")
    return text


def _trimmed(value: str) -> str:
    text = value.strip()
    if not text:
        raise ValueError("must not be blank")
    return text


type SourceTitle = Annotated[str, AfterValidator(_one_line), Field(min_length=1, max_length=200)]
type ScoutQuery = Annotated[str, AfterValidator(_one_line), Field(min_length=3, max_length=300)]
type IdeaTitle = Annotated[str, AfterValidator(_one_line), Field(min_length=1, max_length=300)]
type IdeaNote = Annotated[str, AfterValidator(_trimmed), Field(min_length=1, max_length=2000)]
type IdeaAngle = Annotated[str, AfterValidator(_one_line), Field(min_length=1, max_length=500)]


class SourceIn(ApiModel):
    """A feed (``url`` required, ``https``) or a weekly scouting query (``query`` required)."""

    type: SourceType
    title: SourceTitle
    url: HttpsUrl | None = None
    site_url: HttpsUrl | None = None
    query: ScoutQuery | None = None
    pillar: PillarId | None = None

    @model_validator(mode="after")
    def _shape(self) -> Self:
        if self.type == "feed" and self.url is None:
            raise ValueError("a feed needs its https url")
        if self.type == "scout_query" and self.query is None:
            raise ValueError("a scouting query needs its query")
        return self


class DiscoverIn(ApiModel):
    site_url: HttpsUrl


class DiscoveredFeedOut(ApiModel):
    url: str
    title: str | None = None


class DiscoverOut(ApiModel):
    feeds: list[DiscoveredFeedOut]


class IdeaIn(ApiModel):
    """Quick capture (origin ``quick_capture``)."""

    title: IdeaTitle
    note: IdeaNote | None = None
    url: HttpsUrl | None = None
    pillar: PillarId | None = None
    suggested_lane: IdeaLane | None = None
    time_sensitive: bool = False


class RepurposeIn(ApiModel):
    """``POST /api/ideas/repurpose``: an idea from a post that went out (``recut``: a new angle
    on it as a post; ``flagship``: an article built from it)."""

    post_id: Annotated[str, StringConstraints(min_length=1, max_length=100)]
    kind: Literal["recut", "flagship"] = "recut"


class IdeaEditIn(ApiModel):
    """An owner edit: fields left out stay as they are; ``null`` clears an optional field."""

    title: IdeaTitle | None = None
    note: IdeaNote | None = None
    pillar: PillarId | None = None
    suggested_lane: IdeaLane | None = None
    suggested_format: (
        Annotated[str, StringConstraints(pattern=FORMAT_ID_PATTERN, max_length=40)] | None
    ) = None
    angle: IdeaAngle | None = None

    @model_validator(mode="after")
    def _title_not_cleared(self) -> Self:
        if "title" in self.model_fields_set and self.title is None:
            raise ValueError("an idea keeps its title")
        return self


class IdeaTransitionIn(ApiModel):
    """The owner's status changes (``used`` and ``expired`` are set by the system)."""

    to: Literal["accepted", "snoozed", "dismissed", "new"]
    snoozed_until: Timestamp | None = None
