"""Request and response models of the engagement routes, and the ENGAGEMENT output schemas.

Requests are camelCase and refuse unknown fields (``ApiModel``); they never carry a whole
document: the server sets ids, statuses, timestamps and expiries. ``pastedText`` is other
members' text (untrusted, kept 48 hours); ``draftText`` and ``finalText`` are the owner's.
"""

from __future__ import annotations

from typing import Annotated, Final, Literal, Self

from pydantic import ConfigDict, Field, field_validator, model_validator

from blogwriter.domain.api import ApiModel
from blogwriter.domain.base import DocId, HttpsUrl, Slug
from blogwriter.domain.common import Finding
from blogwriter.engagement._text import MAX_PASTED_CHARS

__all__ = [
    "MAX_DRAFT_CHARS",
    "AngleDraft",
    "AnglesReply",
    "ChecklistIn",
    "ChecklistItemIn",
    "ExtractOut",
    "ExtractReply",
    "FinalizeIn",
    "ItemIn",
    "ItemKind",
    "LintIn",
    "LintOut",
    "WatchIn",
]

MAX_DRAFT_CHARS: Final = 1250
"""LinkedIn's comment limit; the lint warns above 1,000 characters."""

type ItemKind = Literal["reply", "comment"]
type PillarId = Annotated[Slug, Field(max_length=40)]


class ItemIn(ApiModel):
    """A reply (to a comment on the owner's post) or a comment (on someone else's post)."""

    model_config = ConfigDict(str_strip_whitespace=True)

    item_kind: ItemKind
    pasted_text: str = Field(min_length=1, max_length=MAX_PASTED_CHARS)
    post_id: DocId | None = None
    """The owner's post a reply belongs to (replies only)."""
    target_url: HttpsUrl | None = None
    """The LinkedIn post or comment being answered."""

    @model_validator(mode="after")
    def _post_only_for_replies(self) -> Self:
        if self.item_kind == "comment" and self.post_id is not None:
            raise ValueError("postId is only for replies to comments on your own posts")
        return self


class LintIn(ApiModel):
    """A draft to lint. Not trimmed: finding spans index the text exactly as sent."""

    draft_text: str = Field(min_length=1, max_length=MAX_DRAFT_CHARS)

    @field_validator("draft_text")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("the draft is empty")
        return value


class LintOut(ApiModel):
    findings: list[Finding]


class FinalizeIn(ApiModel):
    """What the owner actually posted: the final text and, optionally, its LinkedIn URL.

    ``watchEntryId`` (an extension of the contract) links the comment to a watchlist voice and
    marks that voice as engaged.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    final_text: str = Field(min_length=1, max_length=MAX_DRAFT_CHARS)
    url: HttpsUrl | None = None
    watch_entry_id: DocId | None = None


class WatchIn(ApiModel):
    """A niche voice for the daily session (``PUT`` replaces every field)."""

    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=120)
    profile_url: HttpsUrl | None = None
    pillar: PillarId | None = None
    note: str | None = Field(default=None, max_length=500)
    active: bool = True

    @field_validator("note")
    @classmethod
    def _blank_note_is_none(cls, value: str | None) -> str | None:
        return value or None


class ChecklistItemIn(ApiModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    id: Annotated[Slug, Field(max_length=40)]
    label: str = Field(min_length=1, max_length=200)
    done: bool = False
    note: str | None = Field(default=None, max_length=500)

    @field_validator("note")
    @classmethod
    def _blank_note_is_none(cls, value: str | None) -> str | None:
        return value or None


class ChecklistIn(ApiModel):
    """The whole profile checklist (``doneAt`` is set by the server)."""

    items: list[ChecklistItemIn] = Field(max_length=30)

    @model_validator(mode="after")
    def _unique_ids(self) -> Self:
        ids = [item.id for item in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("checklist item ids must be unique")
        return self


class ExtractOut(ApiModel):
    """The text read from a screenshot, for the owner to check before creating an item.

    Nothing is stored: neither the image nor the text. ``findings`` are the blocklist's on the
    text (term ids only), so the owner sees what to remove first.
    """

    text: str
    findings: list[Finding]


# --------------------------------------------------------------------------------------------
# Structured output of the ENGAGEMENT calls (lenient: the code checks and trims afterwards)
# --------------------------------------------------------------------------------------------


class AngleDraft(ApiModel):
    """One angle as the model wrote it. ``groundedIn`` holds entry refs (``s1``, ``p2``)."""

    text: str = Field(default="", max_length=4000)
    grounded_in: list[str] = Field(default_factory=list[str], max_length=10)


class AnglesReply(ApiModel):
    angles: list[AngleDraft] = Field(default_factory=list[AngleDraft], max_length=6)


class ExtractReply(ApiModel):
    """The words of the post or comments in a screenshot (empty when there are none)."""

    text: str = Field(default="", max_length=20_000)
