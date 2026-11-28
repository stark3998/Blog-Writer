"""Provider-neutral types for the AI layer.

Everything the router, the providers and their callers exchange is defined here:

* :class:`Task` names a unit of AI work (the router maps each task to model settings).
* :class:`Sensitivity` is the data class of a request, ordered ``PUBLIC < ANONYMIZED < PRIVATE``.
* :class:`ModelSpec` says which deployment serves a call and how (effort, output budget).
* :class:`Message`, the tool definitions and :class:`Request` describe one model call. User turns
  may carry :class:`ImagePart` screenshots (size-capped; the policy allows them only for the
  tasks in ``blogwriter.ai.policy.IMAGE_TASKS``).
* :data:`StreamEvent` is the normalized stream: :class:`TextDelta`, :class:`ThinkingSummary`,
  :class:`ToolCall`, :class:`Refusal`, :class:`UsageEvent`, then exactly one :class:`Done`.
* :class:`Completion` is the result of one call, and :class:`Usage` its token and tool counts.
* :class:`Embeddings` is the result of one embeddings call (``Task.EMBED``).
* :class:`ImageRequest` is one image generation (``Task.IMAGE``: a text prompt, one PNG) and
  :class:`ImageGeneration` its result: a :class:`GeneratedImage`, or a :class:`Refusal` when a
  content filter declined (a typed result, never an exception).

The types are frozen dataclasses that validate themselves on construction, so a value that
exists is well formed. Fields that carry prompt or model text are left out of ``repr`` so an
object that ends up in a log line or traceback does not leak content. :class:`Request` also
enforces the sensitivity rules that do not depend on the provider: only ``public`` data reaches
tools or provider-side background runs.
"""

from __future__ import annotations

import base64
import re
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from enum import StrEnum
from typing import Final, Literal

from pydantic import BaseModel

__all__ = [
    "ANTHROPIC_EFFORTS",
    "DEFAULT_MAX_TOOL_CALLS",
    "IMAGE_MEDIA_TYPES",
    "IMAGE_QUALITIES",
    "IMAGE_SIZES",
    "MAX_CACHE_BREAKPOINTS",
    "MAX_GENERATED_IMAGE_BYTES",
    "MAX_IMAGE_BYTES",
    "MAX_IMAGE_PARTS",
    "MAX_IMAGE_PROMPT_CHARS",
    "OPENAI_EFFORTS",
    "Citation",
    "Completion",
    "Done",
    "Effort",
    "Embeddings",
    "FinishReason",
    "FunctionTool",
    "GeneratedImage",
    "ImageGeneration",
    "ImageMediaType",
    "ImagePart",
    "ImageQuality",
    "ImageRequest",
    "ImageSize",
    "Message",
    "ModelSpec",
    "NativeItems",
    "Part",
    "ProviderName",
    "Refusal",
    "Request",
    "Role",
    "Sensitivity",
    "SensitivityError",
    "StreamEvent",
    "Task",
    "TextDelta",
    "TextPart",
    "ThinkingSummary",
    "Tool",
    "ToolCall",
    "ToolCallPart",
    "ToolResultPart",
    "Usage",
    "UsageEvent",
    "WebSearchTool",
    "png_size",
]

type ProviderName = Literal["openai", "anthropic"]
"""The two providers: Azure OpenAI (Responses API, v1 endpoint) and Anthropic on Foundry."""

type Effort = Literal["none", "minimal", "low", "medium", "high", "xhigh", "max"]
"""Reasoning effort. Anthropic accepts only :data:`ANTHROPIC_EFFORTS`."""

OPENAI_EFFORTS: Final[frozenset[str]] = frozenset(
    {"none", "minimal", "low", "medium", "high", "xhigh", "max"}
)
ANTHROPIC_EFFORTS: Final[frozenset[str]] = frozenset({"low", "medium", "high", "xhigh", "max"})

type FinishReason = Literal["stop", "tool_calls", "length", "refusal", "pause", "cancelled"]
"""Why a call ended.

``stop``
    A natural end (end of turn or a stop sequence).
``tool_calls``
    The model asked for function tool calls; run them and continue the loop.
``length``
    The output budget or the context window ran out. The output is truncated.
``refusal``
    The model or a platform filter declined. Partial output must be discarded.
``pause``
    The provider paused a long turn (Anthropic ``pause_turn``); resend to continue.
``cancelled``
    A background run was cancelled. Its partial output is discarded.
"""

type Role = Literal["user", "assistant"]

DEFAULT_MAX_TOOL_CALLS: Final = 8
"""Cap on hosted tool calls (``web_search``) per call when the request does not set one."""

MAX_CACHE_BREAKPOINTS: Final = 3
"""Message-level cache breakpoints per request. Anthropic allows 4; the system prompt uses one."""

type ImageMediaType = Literal["image/png", "image/jpeg", "image/webp", "image/gif"]
"""Image formats both providers accept as input (Responses ``input_image``, Claude ``image``)."""

IMAGE_MEDIA_TYPES: Final[frozenset[str]] = frozenset(
    {"image/png", "image/jpeg", "image/webp", "image/gif"}
)
MAX_IMAGE_BYTES: Final = 5 * 1024 * 1024
"""Largest image part (Claude's per-image limit is 5 MB; OpenAI accepts more)."""
MAX_IMAGE_PARTS: Final = 4
"""Most image parts in one request (screenshot extraction needs one to three)."""

type ImageSize = Literal["1536x1024", "1024x1024"]
"""Generated image sizes: a landscape hero (1536x1024) and a square inline image (1024x1024)."""
IMAGE_SIZES: Final[frozenset[str]] = frozenset({"1536x1024", "1024x1024"})
type ImageQuality = Literal["low", "medium", "high"]
"""GPT image quality (illustrations use ``high``)."""
IMAGE_QUALITIES: Final[frozenset[str]] = frozenset({"low", "medium", "high"})
MAX_IMAGE_PROMPT_CHARS: Final = 4_000
"""Longest image prompt (the API takes 32,000 characters; illustration prompts are short)."""
MAX_GENERATED_IMAGE_BYTES: Final = 24 * 1024 * 1024
"""Largest generated image accepted from the provider (a 1536x1024 PNG is a few MB)."""

_TOOL_NAME = re.compile(r"[A-Za-z0-9_-]{1,64}")
_DOMAIN_LABEL = re.compile(r"(?!-)[a-z0-9-]{1,63}(?<!-)")


class Task(StrEnum):
    """A unit of AI work. The router maps each task to a model, effort and data class."""

    DRAFT = "draft"
    REVISE = "revise"
    OUTLINE = "outline"
    PLAN = "plan"
    INTERVIEW = "interview"
    SYNTHESIS = "synthesis"
    CRITIQUE = "critique"
    FACT_CHECK = "fact_check"
    ANONYMIZE = "anonymize"
    RESEARCH = "research"
    QUICK_SEARCH = "quick_search"
    TRIAGE = "triage"
    ENGAGEMENT = "engagement"
    EMBED = "embed"
    IMAGE = "image"


_SENSITIVITY_RANK: Final[dict[str, int]] = {"public": 0, "anonymized": 1, "private": 2}


class Sensitivity(StrEnum):
    """The data class of a request, ordered ``PUBLIC < ANONYMIZED < PRIVATE``.

    ``private``
        Raw interview text. Never persisted; only a tool-free Azure OpenAI anonymizer sees it.
    ``anonymized``
        Approved story and POV text. May go to Azure OpenAI and Claude, tool-free only.
    ``public``
        Everything else. The only class that tool-enabled and ``web_search`` contexts receive.

    The comparison operators follow that order, not string order, and refuse other types (a
    plain string never compares against a class by accident).
    """

    PUBLIC = "public"
    ANONYMIZED = "anonymized"
    PRIVATE = "private"

    @property
    def rank(self) -> int:
        """0 for ``public``, 1 for ``anonymized``, 2 for ``private``."""
        return _SENSITIVITY_RANK[self.value]

    def permits(self, data: Sensitivity) -> bool:
        """Whether a context cleared for ``self`` may receive data of class ``data``."""
        return data.rank <= self.rank

    @classmethod
    def most_restrictive(cls, first: Sensitivity, *others: Sensitivity) -> Sensitivity:
        """The class of data combined from inputs of the given classes."""
        return max((first, *others), key=lambda item: item.rank)

    def _rank_of(self, other: object) -> int:
        if not isinstance(other, Sensitivity):
            raise TypeError(f"cannot order Sensitivity against {type(other).__name__}")
        return other.rank

    def __lt__(self, other: str) -> bool:
        return self.rank < self._rank_of(other)

    def __le__(self, other: str) -> bool:
        return self.rank <= self._rank_of(other)

    def __gt__(self, other: str) -> bool:
        return self.rank > self._rank_of(other)

    def __ge__(self, other: str) -> bool:
        return self.rank >= self._rank_of(other)


class SensitivityError(ValueError):
    """A request would send data to a context its class does not permit."""


@dataclass(frozen=True, slots=True)
class ModelSpec:
    """Which deployment serves a call, and how.

    ``family`` is the model behind the deployment (for example ``claude-opus-5-5``); pricing and
    limits key on it. ``effort`` is the reasoning effort (``None`` leaves the provider default).
    ``thinking_summary`` asks for reasoning summaries (OpenAI ``reasoning.summary="auto"``,
    Anthropic thinking display ``summarized``); the stream carries them as
    :class:`ThinkingSummary` events.
    """

    provider: ProviderName
    deployment: str
    family: str
    effort: Effort | None
    max_output_tokens: int
    supports_tools: bool
    thinking_summary: bool = True

    def __post_init__(self) -> None:
        if self.provider not in ("openai", "anthropic"):  # pyright: ignore[reportUnnecessaryContains]
            raise ValueError(f"unknown provider {self.provider!r}")
        if not self.deployment.strip() or not self.family.strip():
            raise ValueError("deployment and family must be non-empty")
        if self.max_output_tokens < 1:
            raise ValueError("max_output_tokens must be at least 1")
        allowed = ANTHROPIC_EFFORTS if self.provider == "anthropic" else OPENAI_EFFORTS
        if self.effort is not None and self.effort not in allowed:
            raise ValueError(f"effort {self.effort!r} is not supported by {self.provider}")


# --------------------------------------------------------------------------------------------
# Messages
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TextPart:
    """Text in a message.

    ``cache_breakpoint`` marks the end of a stable prefix (for example a research brief that a
    revise loop resends). Anthropic caches the prefix up to here for 1 hour; OpenAI caches
    prefixes automatically and ignores the flag.
    """

    text: str = field(repr=False)
    cache_breakpoint: bool = False

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise ValueError("text parts must contain non-whitespace text")


@dataclass(frozen=True, slots=True)
class ToolCallPart:
    """A function tool call the assistant made. ``arguments`` is the JSON object text."""

    call_id: str
    name: str
    arguments: str = field(repr=False)

    def __post_init__(self) -> None:
        if not self.call_id or not self.name:
            raise ValueError("tool calls need a call_id and a name")


@dataclass(frozen=True, slots=True)
class ToolResultPart:
    """The result of a function tool call, sent back in a user turn."""

    call_id: str
    output: str = field(repr=False)
    is_error: bool = False

    def __post_init__(self) -> None:
        if not self.call_id:
            raise ValueError("tool results need the call_id they answer")


def _sniff_image(data: bytes) -> str | None:
    """The media type the magic bytes say ``data`` is, or ``None``."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    return None


@dataclass(frozen=True, slots=True)
class ImagePart:
    """An image in a user turn: the owner's own screenshot (for example a LinkedIn comment
    thread to extract text from) or a generated illustration under its vision check.

    At most :data:`MAX_IMAGE_BYTES`; the bytes must really be the declared ``media_type`` (magic
    bytes are checked, so a mislabelled or truncated upload is refused before anything is
    sent). The policy accepts images only for the tasks in ``blogwriter.ai.policy.IMAGE_TASKS``,
    up to the data class listed there. Images cannot be checked against the blocklist the way
    text is: callers send only the owner's own screenshots, classified like the text they show
    (``anonymized`` for other members' comments), or images generated from public prompts
    (``public``). ``data`` is left out of ``repr``.
    """

    data: bytes = field(repr=False)
    media_type: ImageMediaType

    def __post_init__(self) -> None:
        if not isinstance(self.data, bytes):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise TypeError("image data must be bytes")
        if not self.data:
            raise ValueError("image parts need image bytes")
        if len(self.data) > MAX_IMAGE_BYTES:
            raise ValueError(f"image parts may have at most {MAX_IMAGE_BYTES} bytes")
        if self.media_type not in IMAGE_MEDIA_TYPES:
            raise ValueError(f"unsupported image media type {self.media_type!r}")
        if _sniff_image(self.data) != self.media_type:
            raise ValueError(f"the image bytes are not {self.media_type}")

    @property
    def base64(self) -> str:
        """The bytes as standard base64 (no line breaks)."""
        return base64.b64encode(self.data).decode("ascii")

    @property
    def data_url(self) -> str:
        """``data:<media type>;base64,<bytes>`` (the form Responses ``input_image`` takes)."""
        return f"data:{self.media_type};base64,{self.base64}"


type Part = TextPart | ImagePart | ToolCallPart | ToolResultPart


@dataclass(frozen=True, slots=True)
class NativeItems:
    """An assistant turn exactly as one provider returned it, for lossless replay.

    OpenAI tool loops are stateless (``store=false``), so the reasoning items with their
    ``encrypted_content`` must be sent back verbatim; Anthropic needs its thinking blocks back
    with their signatures. The items are opaque JSON objects and are only ever sent back to the
    provider that produced them. Another provider falls back to the message's generic parts.
    """

    provider: ProviderName
    items: tuple[Mapping[str, object], ...] = field(repr=False)

    def __post_init__(self) -> None:
        if not self.items:
            raise ValueError("native items must not be empty")


@dataclass(frozen=True, slots=True)
class Message:
    """One conversation turn.

    User turns hold :class:`TextPart`, :class:`ImagePart` and :class:`ToolResultPart`; assistant
    turns hold :class:`TextPart` and :class:`ToolCallPart`, plus optional :class:`NativeItems` for
    replay.
    """

    role: Role
    parts: tuple[Part, ...]
    native: NativeItems | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "parts", tuple(self.parts))
        if self.role not in ("user", "assistant"):  # pyright: ignore[reportUnnecessaryContains]
            raise ValueError(f"unknown role {self.role!r}")
        if not self.parts and self.native is None:
            raise ValueError("a message needs at least one part")
        for part in self.parts:
            if isinstance(part, ToolCallPart) and self.role != "assistant":
                raise ValueError("tool calls belong to assistant turns")
            if isinstance(part, ToolResultPart) and self.role != "user":
                raise ValueError("tool results belong to user turns")
            if isinstance(part, ImagePart) and self.role != "user":
                raise ValueError("images belong to user turns")
        if self.native is not None and self.role != "assistant":
            raise ValueError("native items belong to assistant turns")

    @classmethod
    def user(cls, *texts: str) -> Message:
        """A user turn made of text."""
        return cls("user", tuple(TextPart(text) for text in texts))

    @classmethod
    def assistant(cls, text: str) -> Message:
        """An assistant turn made of text (for example an earlier reply)."""
        return cls("assistant", (TextPart(text),))

    @classmethod
    def tool_results(cls, *results: ToolResultPart) -> Message:
        """A user turn that answers the assistant's tool calls."""
        return cls("user", results)

    @property
    def text(self) -> str:
        """The concatenated text parts."""
        return "".join(part.text for part in self.parts if isinstance(part, TextPart))

    @property
    def images(self) -> tuple[ImagePart, ...]:
        """The image parts, in order."""
        return tuple(part for part in self.parts if isinstance(part, ImagePart))


# --------------------------------------------------------------------------------------------
# Tools
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FunctionTool:
    """A function the model may call. ``parameters`` is the Pydantic model of its arguments."""

    name: str
    description: str
    parameters: type[BaseModel]

    def __post_init__(self) -> None:
        if not _TOOL_NAME.fullmatch(self.name):
            raise ValueError(f"tool name {self.name!r} must match [A-Za-z0-9_-]{{1,64}}")
        if not self.description.strip():
            raise ValueError("tools need a description")
        if not (isinstance(self.parameters, type) and issubclass(self.parameters, BaseModel)):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise TypeError("tool parameters must be a pydantic BaseModel subclass")


@dataclass(frozen=True, slots=True)
class WebSearchTool:
    """The hosted ``web_search`` tool (OpenAI Responses; backed by Web IQ on Azure).

    ``allowed_domains`` limits results to those domains and their subdomains. Domains are bare
    host names: no scheme, path, port or wildcard.
    """

    allowed_domains: tuple[str, ...] = ()
    search_context_size: Literal["low", "medium", "high"] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "allowed_domains", tuple(self.allowed_domains))
        for domain in self.allowed_domains:
            labels = domain.split(".")
            if (
                domain != domain.lower()
                or len(domain) > 253
                or len(labels) < 2
                or not all(_DOMAIN_LABEL.fullmatch(label) for label in labels)
            ):
                raise ValueError(f"allowed domain {domain!r} is not a bare lowercase host name")


type Tool = FunctionTool | WebSearchTool


# --------------------------------------------------------------------------------------------
# Request
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class Request:
    """One model call, independent of the provider.

    ``sensitivity`` is the class of the most restrictive data anywhere in the request (system
    prompt, messages, tool results). It has no default: every caller classifies its data.

    ``background`` runs the call as an OpenAI background response: stored provider-side until it
    is deleted, polled by id. Only ``public`` requests may run in the background, and only
    ``public`` requests may carry tools.

    ``max_tool_calls`` caps hosted tool calls (``web_search``); requests with tools get
    :data:`DEFAULT_MAX_TOOL_CALLS` when it is ``None``.

    At most :data:`MAX_IMAGE_PARTS` :class:`ImagePart` in the whole request.
    """

    system: str = field(repr=False)
    messages: tuple[Message, ...]
    sensitivity: Sensitivity
    output_schema: type[BaseModel] | None = None
    tools: tuple[Tool, ...] = ()
    background: bool = False
    max_tool_calls: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "messages", tuple(self.messages))
        object.__setattr__(self, "tools", tuple(self.tools))
        if not isinstance(self.sensitivity, Sensitivity):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise TypeError("sensitivity must be a Sensitivity")
        if not self.messages:
            raise ValueError("a request needs at least one message")
        if self.messages[0].role != "user" or self.messages[-1].role != "user":
            raise ValueError("a request must start and end with a user turn")
        if self.output_schema is not None and not (
            isinstance(self.output_schema, type) and issubclass(self.output_schema, BaseModel)  # pyright: ignore[reportUnnecessaryIsInstance]
        ):
            raise TypeError("output_schema must be a pydantic BaseModel subclass")
        if self.max_tool_calls is not None and self.max_tool_calls < 1:
            raise ValueError("max_tool_calls must be at least 1")
        names = [tool.name for tool in self.tools if isinstance(tool, FunctionTool)]
        if len(names) != len(set(names)):
            raise ValueError("tool names must be unique")
        if sum(isinstance(tool, WebSearchTool) for tool in self.tools) > 1:
            raise ValueError("at most one web_search tool")
        breakpoints = sum(
            isinstance(part, TextPart) and part.cache_breakpoint
            for message in self.messages
            for part in message.parts
        )
        if breakpoints > MAX_CACHE_BREAKPOINTS:
            raise ValueError(f"at most {MAX_CACHE_BREAKPOINTS} message cache breakpoints")
        if self.image_count > MAX_IMAGE_PARTS:
            raise ValueError(f"at most {MAX_IMAGE_PARTS} images per request")
        if self.tools and self.sensitivity is not Sensitivity.PUBLIC:
            raise SensitivityError("only public data may reach a tool-enabled context")
        if self.background and self.sensitivity is not Sensitivity.PUBLIC:
            raise SensitivityError("only public data may run as a stored background response")

    @property
    def tool_call_cap(self) -> int:
        """The hosted tool call cap to send when the request has tools."""
        return self.max_tool_calls if self.max_tool_calls is not None else DEFAULT_MAX_TOOL_CALLS

    @property
    def image_count(self) -> int:
        """How many :class:`ImagePart` the messages carry."""
        return sum(isinstance(part, ImagePart) for m in self.messages for part in m.parts)


# --------------------------------------------------------------------------------------------
# Usage, completion and stream events
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Usage:
    """Token and tool counts of one call (or a sum of calls, with ``+``).

    ``input`` counts every prompt token. ``cached_input`` (read from the prompt cache) and
    ``cache_write`` (written to it) are subsets of ``input``; Anthropic cache writes made here
    always use the 1 hour TTL. ``output`` counts every generated token and ``reasoning`` is the
    part of it spent on reasoning. ``web_search_requests`` counts billable hosted searches and
    ``images`` generated images.
    """

    input: int = 0
    cached_input: int = 0
    cache_write: int = 0
    output: int = 0
    reasoning: int = 0
    web_search_requests: int = 0
    images: int = 0

    def __post_init__(self) -> None:
        for item in fields(self):
            if getattr(self, item.name) < 0:
                raise ValueError(f"usage {item.name} must not be negative")

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            input=self.input + other.input,
            cached_input=self.cached_input + other.cached_input,
            cache_write=self.cache_write + other.cache_write,
            output=self.output + other.output,
            reasoning=self.reasoning + other.reasoning,
            web_search_requests=self.web_search_requests + other.web_search_requests,
            images=self.images + other.images,
        )

    @property
    def uncached_input(self) -> int:
        """Prompt tokens billed at the base input rate."""
        return max(self.input - self.cached_input - self.cache_write, 0)


@dataclass(frozen=True, slots=True)
class TextDelta:
    """A piece of the answer text."""

    text: str = field(repr=False)
    type: Literal["text_delta"] = field(default="text_delta", init=False)


@dataclass(frozen=True, slots=True)
class ThinkingSummary:
    """A piece of a reasoning summary (display only; never part of the answer)."""

    text: str = field(repr=False)
    type: Literal["thinking_summary"] = field(default="thinking_summary", init=False)


@dataclass(frozen=True, slots=True)
class ToolCall:
    """A complete function tool call. ``arguments`` is the JSON object text."""

    call_id: str
    name: str
    arguments: str = field(repr=False)
    type: Literal["tool_call"] = field(default="tool_call", init=False)


@dataclass(frozen=True, slots=True)
class Refusal:
    """The model or a platform filter declined.

    ``category`` is the provider's machine-readable reason when it gives one: Anthropic stop
    details (``cyber``, ``bio``, ...), ``content_filter`` for Azure filters, or an OpenAI policy
    error code. ``explanation`` is the provider's human-readable reason, if any. Any partial output
    of a refused call must be discarded and never checkpointed.
    """

    category: str | None
    explanation: str | None
    type: Literal["refusal"] = field(default="refusal", init=False)


@dataclass(frozen=True, slots=True)
class Citation:
    """A source the answer cites (``url_citation`` annotations)."""

    url: str
    title: str | None


@dataclass(frozen=True, slots=True)
class Completion:
    """The result of one call.

    ``text`` is the answer (raw JSON when the request had an ``output_schema``; validate it with
    :mod:`blogwriter.ai.schema`). It is empty when the call was refused or cancelled.
    ``message`` is the assistant turn to append when continuing a tool loop (``None`` after a
    refusal or cancellation). ``search_queries`` and ``search_sources`` list what the hosted
    ``web_search`` asked and which URLs it returned, for the blocklist check and the
    ``fetch_page`` allowlist.
    """

    text: str = field(repr=False)
    finish_reason: FinishReason
    usage: Usage
    model: str
    response_id: str | None
    tool_calls: tuple[ToolCall, ...] = ()
    refusal: Refusal | None = None
    message: Message | None = None
    citations: tuple[Citation, ...] = ()
    search_queries: tuple[str, ...] = ()
    search_sources: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if (self.finish_reason == "refusal") != (self.refusal is not None):
            raise ValueError("a refusal completion carries exactly one Refusal")
        if self.finish_reason == "refusal" and (
            self.text or self.tool_calls or self.message is not None
        ):
            raise ValueError("a refused completion must not carry output")


@dataclass(frozen=True, slots=True)
class UsageEvent:
    """The final usage of the call (sent once, before :class:`Done`)."""

    usage: Usage
    type: Literal["usage"] = field(default="usage", init=False)


@dataclass(frozen=True, slots=True)
class Done:
    """The last event of every successful stream, with the assembled completion."""

    finish_reason: FinishReason
    completion: Completion
    type: Literal["done"] = field(default="done", init=False)


type StreamEvent = TextDelta | ThinkingSummary | ToolCall | Refusal | UsageEvent | Done


# --------------------------------------------------------------------------------------------
# Embeddings
# --------------------------------------------------------------------------------------------


def png_size(data: bytes) -> tuple[int, int] | None:
    """``(width, height)`` from a PNG's IHDR chunk, or ``None`` when ``data`` is not a PNG."""
    if len(data) < 24 or not data.startswith(b"\x89PNG\r\n\x1a\n") or data[12:16] != b"IHDR":
        return None
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    if width < 1 or height < 1:
        return None
    return width, height


@dataclass(frozen=True, slots=True, kw_only=True)
class ImageRequest:
    """One image generation (``Task.IMAGE``): a text prompt, one PNG of ``size`` at ``quality``.

    ``sensitivity`` has no default, like :class:`Request`: every caller classifies its prompt.
    The image route's ceiling is ``public``, so only public data (article metadata, an art
    brief) may ever reach an image model. The prompt is left out of ``repr``.
    """

    prompt: str = field(repr=False)
    sensitivity: Sensitivity
    size: ImageSize = "1536x1024"
    quality: ImageQuality = "high"

    def __post_init__(self) -> None:
        if not isinstance(self.sensitivity, Sensitivity):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise TypeError("sensitivity must be a Sensitivity")
        if not self.prompt.strip():
            raise ValueError("an image request needs a prompt")
        if len(self.prompt) > MAX_IMAGE_PROMPT_CHARS:
            raise ValueError(f"image prompts may have at most {MAX_IMAGE_PROMPT_CHARS} characters")
        if self.size not in IMAGE_SIZES:
            raise ValueError(f"unsupported image size {self.size!r}")
        if self.quality not in IMAGE_QUALITIES:
            raise ValueError(f"unsupported image quality {self.quality!r}")

    @property
    def width(self) -> int:
        return int(self.size.split("x")[0])

    @property
    def height(self) -> int:
        return int(self.size.split("x")[1])

    def policy_view(self) -> Request:
        """The request as the sensitivity policy and the limiter see it (a text-only turn)."""
        return Request(
            system="", messages=(Message.user(self.prompt),), sensitivity=self.sensitivity
        )


@dataclass(frozen=True, slots=True)
class GeneratedImage:
    """A generated PNG exactly as the provider returned it (metadata included: re-encode it
    before storing). ``data`` is left out of ``repr``."""

    data: bytes = field(repr=False)
    width: int
    height: int
    media_type: Literal["image/png"] = "image/png"

    def __post_init__(self) -> None:
        if not isinstance(self.data, bytes):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise TypeError("image data must be bytes")
        if not self.data or len(self.data) > MAX_GENERATED_IMAGE_BYTES:
            raise ValueError(f"generated images have 1 to {MAX_GENERATED_IMAGE_BYTES} bytes")
        if png_size(self.data) != (self.width, self.height):
            raise ValueError("the image bytes are not a PNG of the stated size")


@dataclass(frozen=True, slots=True)
class ImageGeneration:
    """The result of one image call: exactly one of ``image`` and ``refusal``.

    A refusal (``category`` ``content_filter`` for Azure's filters and OpenAI's moderation) is
    a result, not an error: nothing was generated and nothing is retried.
    """

    image: GeneratedImage | None
    usage: Usage
    model: str
    refusal: Refusal | None = None

    def __post_init__(self) -> None:
        if (self.image is None) == (self.refusal is None):
            raise ValueError("an image result carries exactly one of an image and a refusal")

    @property
    def refused(self) -> bool:
        return self.refusal is not None


@dataclass(frozen=True, slots=True)
class Embeddings:
    """The result of one embeddings call: one vector per input, in input order.

    ``usage.input`` counts the prompt tokens (embeddings have no output tokens). ``model`` is
    the model the service reports. The vectors are left out of ``repr``.
    """

    vectors: tuple[tuple[float, ...], ...] = field(repr=False)
    usage: Usage
    model: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "vectors", tuple(tuple(vector) for vector in self.vectors))
        if not self.vectors:
            raise ValueError("an embeddings result needs at least one vector")
        width = len(self.vectors[0])
        if width < 1 or any(len(vector) != width for vector in self.vectors):
            raise ValueError("embedding vectors must be non-empty and of one size")

    @property
    def dimensions(self) -> int:
        return len(self.vectors[0])
