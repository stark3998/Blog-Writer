"""Request and response models of the illustration routes, the structured outputs of the plan
and the vision check, and the job's checkpointed state.

Request models are :class:`~blogwriter.domain.api.ApiModel` (camelCase, unknown fields
refused). The output schemas use plain fields with their size rules as descriptions (a reply a
little too long is trimmed in code instead of burning a repair turn). The job state holds
references, counts and short model-written texts (the plan), never image bytes.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from blogwriter.domain.api import ApiModel, Versioned
from blogwriter.domain.articles import MAX_ILLUSTRATION_ALT, Article, IllustrationRole
from blogwriter.domain.common import ServedByRecord

__all__ = [
    "AltIn",
    "BriefOut",
    "IllustrationJobState",
    "IllustrationsAccepted",
    "IllustrationsIn",
    "ImagePhase",
    "ImageState",
    "InsertIn",
    "PlanOut",
    "PlanState",
    "PlannedImage",
    "StoredBlob",
    "VerdictOut",
]

# --------------------------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------------------------


class IllustrationsIn(ApiModel):
    """``POST /api/articles/{id}/images``: one hero plus ``count - 1`` inline illustrations."""

    count: int = Field(default=1, ge=1, le=3)
    direction: str | None = Field(default=None, min_length=1, max_length=300)
    """A short art direction ("warmer light, a bridge rather than a gate"). Public data: it
    shapes an image prompt, so it is screened against the blocklist."""


class IllustrationsAccepted(ApiModel):
    """202 answer: the article (it names the run in ``illustrationJobId``) and the job."""

    article: Versioned[Article]
    job_id: str


class InsertIn(ApiModel):
    """``POST .../images/{illustrationId}/insert``: the section heading to insert after (its
    text as the reader sees it; case and inline markup are ignored)."""

    heading: str = Field(min_length=1, max_length=200)


class AltIn(ApiModel):
    """``PUT .../images/{illustrationId}/alt``."""

    alt_text: str = Field(min_length=1, max_length=MAX_ILLUSTRATION_ALT)


# --------------------------------------------------------------------------------------------
# Structured outputs (public data, tool-free)
# --------------------------------------------------------------------------------------------


class BriefOut(ApiModel):
    role: Literal["hero", "inline"] = Field(
        description='"hero" for the first illustration, "inline" for the others.'
    )
    section: str = Field(
        description="For an inline illustration: the section heading it follows, copied exactly "
        "from the list. An empty string for the hero."
    )
    concept: str = Field(
        description="One sentence: the single idea the image shows, as a concrete visual "
        "metaphor. Under 200 characters."
    )
    prompt: str = Field(
        description="The image prompt, 40 to 120 words: subject, composition, perspective, "
        "materials, light and mood, in the art brief's style and palette. No text in the image."
    )
    alt_text: str = Field(
        description="One plain sentence describing what the image shows for a reader who cannot "
        "see it, under 200 characters."
    )


class PlanOut(ApiModel):
    """PLAN: the illustrations, hero first."""

    illustrations: list[BriefOut] = Field(
        description="The hero first, then the inline illustrations: exactly as many as asked."
    )


class VerdictOut(ApiModel):
    """FACT_CHECK with the image: the "media matches text" verdict."""

    depicts_concept: bool = Field(description="The image clearly shows the concept.")
    legible_text: bool = Field(
        description="Any legible or nearly legible text, letters, numbers or pseudo-writing."
    )
    logos_or_branding: bool = Field(
        description="Any logo, brand mark, product name or real company's branding."
    )
    recognisable_person: bool = Field(
        description="A recognisable real person or a detailed human face."
    )
    unsafe: bool = Field(description="Anything unsuitable for a professional blog.")
    reason: str = Field(
        description="One short sentence naming what failed; empty when everything passes."
    )

    def failures(self) -> list[str]:
        """The failed checks as codes (empty: the image passed)."""
        codes: list[str] = []
        if not self.depicts_concept:
            codes.append("off_concept")
        if self.legible_text:
            codes.append("legible_text")
        if self.logos_or_branding:
            codes.append("branding")
        if self.recognisable_person:
            codes.append("person")
        if self.unsafe:
            codes.append("unsafe")
        return codes


# --------------------------------------------------------------------------------------------
# Job state (step outputs and checkpoints)
# --------------------------------------------------------------------------------------------

type ImagePhase = Literal["new", "pending", "stored", "passed", "dropped"]
"""``new``: nothing generated for the current attempt; ``pending``: a generation call was
recorded before it was awaited (after a crash its result is unknown); ``stored``: the image is
in Blob (re-encoded), not checked yet; ``passed``: the vision check passed; ``dropped``: given
up (``reasons`` say why)."""


class PlannedImage(ApiModel):
    """One planned illustration (the ``plan`` step's output)."""

    index: int = Field(ge=0, le=2)
    role: IllustrationRole
    size: Literal["1536x1024", "1024x1024"]
    concept: str = Field(min_length=1, max_length=300)
    prompt: str = Field(min_length=1, max_length=4000)
    """The full image prompt: the model's description plus the fixed style and safety rules."""
    retry_prompt: str = Field(min_length=1, max_length=4000)
    """The adjusted prompt of the one automatic retry (simpler, more abstract)."""
    alt_text: str = Field(min_length=1, max_length=MAX_ILLUSTRATION_ALT)
    section: str | None = Field(default=None, max_length=200)


class PlanState(ApiModel):
    """The ``plan`` step's output: the planned images and the plan call's record."""

    images: list[PlannedImage] = Field(min_length=1, max_length=3)
    served_by: ServedByRecord
    cost_usd: float = Field(default=0.0, ge=0)


class StoredBlob(ApiModel):
    """A generated image after re-encoding, in Blob ``media`` (content-addressed)."""

    name: str
    sha256: str
    size_bytes: int = Field(ge=1)
    width: int = Field(ge=1)
    height: int = Field(ge=1)
    content_type: Literal["image/png", "image/jpeg", "image/webp"]


class ImageState(ApiModel):
    """One planned image through generation and its check."""

    index: int = Field(ge=0, le=2)
    attempt: int = Field(default=1, ge=1, le=2)
    """1: the planned prompt; 2: the adjusted prompt after a failed check or a refusal."""
    calls: int = Field(default=0, ge=0)
    """Generation calls made for this image (a crash after a call counts it; bounded)."""
    phase: ImagePhase = "new"
    blob: StoredBlob | None = None
    discarded: list[str] = Field(default_factory=list[str])
    """Blob names of images that failed their check (deleted by the ``store`` step)."""
    reasons: list[str] = Field(default_factory=list[str])
    """Why attempts failed (``content_filter``, ``legible_text``, ``interrupted``...)."""
    served_by: list[ServedByRecord] = Field(default_factory=list[ServedByRecord])
    cost_usd: float = Field(default=0.0, ge=0)


class IllustrationJobState(ApiModel):
    """The ``generate`` and ``check`` steps' state (checkpointed after every change)."""

    images: list[ImageState]
