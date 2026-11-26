r"""LinkedIn "little text": the token model and serializer for post commentary.

LinkedIn's Posts API reads a post's ``commentary`` as *little text*, a small markup language with 15
reserved characters::

    | { } @ [ ] ( ) < > # \ * _ ~

An unescaped reserved character makes LinkedIn truncate the post silently (the create call still
returns HTTP 201), so every reserved character in the owner's text goes out with a backslash in
front of it. Two constructs are the exception and go out unescaped:

* an approved hashtag is sent as plain ``#Tag``, which LinkedIn turns into a hashtag link (an
  escaped ``\#Tag`` stays plain text);
* a mention uses the little-text syntax ``@[Name](urn:li:person:...)``.

A post is a sequence of tokens (:data:`Token`):

``Text``
    Literal text. Its content is always escaped.
``Hashtag``
    An approved hashtag, stored without the ``#``.
``Mention``
    A person or organization mention: display name and URN.
``Link``
    An external URL. Links are an exception to the zero-links strategy: :func:`api_commentary`
    refuses them unless the caller passes ``allow_links=True``, and each one goes on a line of its
    own. LinkedIn has no link syntax (it auto-links URLs), so the URL is escaped like text.

A post has two representations, kept in lockstep:

* :func:`display_text`: what readers see. It is the kit text and the basis for counts.
* :func:`api_commentary`: the escaped ``commentary`` payload for ``/rest/posts``.

For every valid token sequence, ``unescape(api_commentary(tokens)) == display_text(tokens)``.

:func:`parse_display` turns display text the owner typed into tokens. It recognises hashtags from
the approved list only (case-insensitively; the token carries the approved spelling). Everything
else stays literal text and is escaped: ``C#``, ``foo#bar``, unapproved tags, and anything that
looks like a mention or other little-text markup.

A hashtag only counts as a whole word. The ``#`` must come at the start of the text or after a
space, tab or newline. The tag must be followed by the end of the text or by a space, tab or
newline, optionally after closing punctuation (``. , ; : ! ? ) ] }`` and closing quotes). The tag
is measured in whole grapheme clusters, so a combining mark, joiner or emoji attached to it keeps
the whole word literal. The serializer applies the same rule to hand-built token sequences and
refuses a hashtag that would run into its neighbours: ``C`` + ``#Sharp`` reads as ``C#Sharp``, and
``#AI`` + ``Security`` would publish the unapproved ``#AISecurity``.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final
from urllib.parse import urlsplit

import regex

__all__ = [
    "RESERVED_CHARACTERS",
    "Hashtag",
    "Link",
    "LinkNotAllowedError",
    "LittleTextError",
    "Mention",
    "Text",
    "Token",
    "api_commentary",
    "display_text",
    "escape",
    "parse_display",
    "unescape",
]

#: The little-text reserved characters. Inside text, each one is escaped with a backslash.
RESERVED_CHARACTERS: Final[frozenset[str]] = frozenset("|{}@[]()<>#\\*_~")

# str.translate reads each source character exactly once, so escaping is a single pass: the
# backslash put in front of one character is never itself escaped again.
_ESCAPES: Final = str.maketrans({ch: "\\" + ch for ch in RESERVED_CHARACTERS})

# The reserved characters as the body of a regex character class.
_RESERVED: Final = r"|{}@\[\]()<>\#\\*_~"

_URN: Final = r"urn:li:(?:person:[0-9A-Za-z_-]+|organization:[0-9]+)"
_URN_RE: Final = regex.compile(_URN)

# Text may hold anything except control characters (newline and tab aside) and lone surrogates.
_TEXT_FORBIDDEN: Final = regex.compile(r"(?![\t\n])[\p{Cc}\p{Cs}]")
# A mention name sits inside the mention syntax: one line, no reserved characters.
_NAME_FORBIDDEN: Final = regex.compile(r"[\p{Cc}\p{Cs}\p{Zl}\p{Zp}" + _RESERVED + "]")
# A hashtag goes out unescaped, so its tag is letters, combining marks and decimal digits only.
_TAG: Final = regex.compile(r"[\p{L}\p{Nd}][\p{L}\p{M}\p{Nd}]*")
_LETTER: Final = regex.compile(r"\p{L}")

# An absolute http(s) URL in ASCII, using RFC 3986 characters only (percent-encode anything else).
_URL: Final = regex.compile(r"https?://[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+")
_BAD_PERCENT: Final = regex.compile(r"%(?![0-9A-Fa-f]{2})")

_SPACE: Final = frozenset(" \t\n")
# A grapheme cluster that continues a word: it starts with a letter, mark, number, connector
# punctuation (such as "_") or a joiner.
_WORD_CLUSTER: Final = r"(?=[\p{L}\p{M}\p{N}\p{Pc}\p{Join_Control}])\X"
# What may follow a hashtag: optional closing punctuation, then whitespace or the end of the text.
_TAIL: Final = r"""[.,;:!?)\]}"'\u2019\u201d]*(?:[\x20\t\n]|\Z)"""
_TAIL_RE: Final = regex.compile(_TAIL)
_HASHTAG: Final = regex.compile(
    r"(?<![^\x20\t\n])"  # at the start of the text, or after a space, tab or newline
    r"\#(?P<body>(?>(?:" + _WORD_CLUSTER + r")+))"  # the whole word, in grapheme clusters
    r"(?=" + _TAIL + ")"
)

# One alternative per little-text construct, tried left to right by unescape().
_LITTLE_TEXT: Final = regex.compile(
    r"\\(?P<escaped>[" + _RESERVED + "])"
    r"|@\[(?P<name>(?:\\[" + _RESERVED + "]|[^" + _RESERVED + r"\n])+)\]\((?P<urn>" + _URN + r")\)"
    r"|\{hashtag\|\\\#\|(?P<tag>[^" + _RESERVED + r"\s]+)\}"
    r"|(?P<literal>\#|[^" + _RESERVED + "]+)"
)
_ESCAPED_CHAR: Final = regex.compile(r"\\([" + _RESERVED + "])")


class LittleTextError(ValueError):
    """Input that cannot be represented safely as LinkedIn little text."""


class LinkNotAllowedError(LittleTextError):
    """A :class:`Link` reached :func:`api_commentary` without ``allow_links=True``."""


@dataclass(frozen=True, slots=True)
class Text:
    """Literal text. Newlines and tabs are allowed; other control characters are not."""

    text: str

    def __post_init__(self) -> None:
        if (bad := _TEXT_FORBIDDEN.search(self.text)) is not None:
            raise LittleTextError(
                f"text must not contain U+{ord(bad.group()):04X} "
                "(control characters other than newline and tab, or lone surrogates)"
            )


@dataclass(frozen=True, slots=True)
class Hashtag:
    """An approved hashtag, stored without the ``#``: ``Hashtag("AISecurity")``.

    The tag is letters, combining marks and decimal digits (at least one letter, NFC), so it can go
    out unescaped. Approval is the caller's job: :func:`parse_display` only creates hashtags from
    the approved list.
    """

    tag: str

    def __post_init__(self) -> None:
        if _TAG.fullmatch(self.tag) is None or _LETTER.search(self.tag) is None:
            raise LittleTextError(
                f"invalid hashtag {self.tag!r}: use letters and digits only, with at least one "
                "letter and no leading '#'"
            )
        if not unicodedata.is_normalized("NFC", self.tag):
            raise LittleTextError(f"hashtag {self.tag!r} is not in Unicode NFC form")


@dataclass(frozen=True, slots=True)
class Mention:
    """A mention: ``@[name](urn)`` in the payload, ``@name`` in the display text.

    ``urn`` is ``urn:li:person:<id>`` or ``urn:li:organization:<number>``. ``name`` is one line
    without reserved characters or surrounding whitespace. Names that would need escapes inside the
    mention syntax are refused: LinkedIn's handling of those is unverified.
    """

    name: str
    urn: str

    def __post_init__(self) -> None:
        if _URN_RE.fullmatch(self.urn) is None:
            raise LittleTextError(
                f"invalid mention URN {self.urn!r}: expected urn:li:person:<id> or "
                "urn:li:organization:<number>"
            )
        if not self.name or self.name != self.name.strip():
            raise LittleTextError(f"invalid mention name {self.name!r}: empty or padded")
        if (bad := _NAME_FORBIDDEN.search(self.name)) is not None:
            raise LittleTextError(f"mention name {self.name!r} must not contain {bad.group()!r}")


@dataclass(frozen=True, slots=True)
class Link:
    """An absolute ``http(s)`` URL in ASCII, shown as-is on a line of its own.

    The payload escapes it like text. Whether LinkedIn still auto-links a URL whose reserved
    characters are escaped is for the Phase 0 live golden test to confirm; escaping is the side
    that cannot truncate the post.
    """

    url: str

    def __post_init__(self) -> None:
        if _URL.fullmatch(self.url) is None or _BAD_PERCENT.search(self.url) is not None:
            raise LittleTextError(f"invalid link {self.url!r}: expected an absolute http(s) URL")
        try:
            parts = urlsplit(self.url)
            _ = parts.port  # raises ValueError for a malformed port
        except ValueError as exc:
            raise LittleTextError(f"invalid link {self.url!r}: {exc}") from exc
        if not parts.hostname or "@" in parts.netloc:
            raise LittleTextError(f"invalid link {self.url!r}: needs a host and no user info")


type Token = Text | Hashtag | Mention | Link


def escape(text: str) -> str:
    """Backslash-escape every reserved character in ``text``, in a single pass."""
    return text.translate(_ESCAPES)


def unescape(commentary: str) -> str:
    r"""Return the text LinkedIn displays for a little-text ``commentary`` payload.

    This inverts :func:`api_commentary`: an escape becomes the literal character, ``@[Name](urn)``
    becomes ``@Name``, and plain ``#Tag`` stays as it is. LinkedIn's hashtag template,
    ``{hashtag|\#|Tag}``, also reads as ``#Tag``. Raises :class:`LittleTextError` on anything
    LinkedIn would truncate or misread: a stray reserved character, or a backslash that does not
    escape a reserved character.
    """
    out: list[str] = []
    pos = 0
    while pos < len(commentary):
        match = _LITTLE_TEXT.match(commentary, pos)
        if match is None:
            ch = commentary[pos]
            what = "backslash that escapes nothing" if ch == "\\" else f"unescaped {ch!r}"
            raise LittleTextError(f"malformed little text: {what} at offset {pos}")
        if (escaped := match.group("escaped")) is not None:
            out.append(escaped)
        elif (name := match.group("name")) is not None:
            out.append("@" + _ESCAPED_CHAR.sub(r"\1", name))
        elif (tag := match.group("tag")) is not None:
            out.append("#" + tag)
        else:
            out.append(match.group())
        pos = match.end()
    return "".join(out)


def display_text(tokens: Iterable[Token]) -> str:
    """The text readers see: the kit text and the basis for counts and previews."""
    return _render(tokens)[0]


def api_commentary(tokens: Iterable[Token], *, allow_links: bool = False) -> str:
    """The escaped ``commentary`` payload for ``POST /rest/posts``.

    Text is escaped in a single pass per run, approved hashtags go out as plain ``#Tag``, mentions
    as ``@[Name](urn)``. Raises :class:`LinkNotAllowedError` if a :class:`Link` is present and
    ``allow_links`` is false, and :class:`LittleTextError` if a hashtag would run into its
    neighbours.
    """
    items = tuple(tokens)
    if not allow_links and any(isinstance(token, Link) for token in items):
        raise LinkNotAllowedError(
            "the post contains a link; links are a logged per-post exception (allow_links=True)"
        )
    return _render(items)[1]


def parse_display(text: str, approved_hashtags: Iterable[str]) -> list[Token]:
    """Tokenize display text the owner typed.

    Only hashtags on ``approved_hashtags`` become :class:`Hashtag` tokens, matched regardless of
    case and Unicode normalization; the token carries the approved spelling. Every other character,
    ``#`` included, stays in :class:`Text`, and adjacent text forms a single run. Line breaks are
    normalized to ``\\n``. Mentions and links are never inferred from text; build those tokens
    explicitly.

    Approved entries may carry a leading ``#``. Raises :class:`LittleTextError` for an invalid
    entry, for two entries that differ only in case, and for control characters in ``text``.
    """
    if isinstance(approved_hashtags, str):
        raise TypeError("approved_hashtags must be a collection of tags, not a single string")
    approved = _approved(approved_hashtags)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    tokens: list[Token] = []
    pos = 0
    if approved:
        for match in _HASHTAG.finditer(text):
            canonical = approved.get(_fold(match.group("body")))
            if canonical is None:
                continue
            if match.start() > pos:
                tokens.append(Text(text[pos : match.start()]))
            tokens.append(Hashtag(canonical))
            pos = match.end()
    if pos < len(text):
        tokens.append(Text(text[pos:]))
    return tokens


def _render(tokens: Iterable[Token]) -> tuple[str, str]:
    """Lay ``tokens`` out; return ``(display text, payload)``."""
    display: list[str] = []
    payload: list[str] = []
    hashtag_spans: list[tuple[int, int]] = []  # offsets in the display text
    size = 0
    after_link = False
    for token in tokens:
        is_link = is_hashtag = False
        match token:
            case Text(text=text):
                shown, sent = text, escape(text)
            case Hashtag(tag=tag):
                shown = sent = "#" + tag
                is_hashtag = True
            case Mention(name=name, urn=urn):
                shown, sent = "@" + name, f"@[{name}]({urn})"
            case Link(url=url):
                shown, sent = url, escape(url)
                is_link = True
            case _:
                raise TypeError(f"not a little-text token: {token!r}")
        if not shown:
            continue
        # A link sits on a line of its own: break before and after it unless a newline is there.
        if (is_link and size and not display[-1].endswith("\n")) or (
            after_link and not shown.startswith("\n")
        ):
            display.append("\n")
            payload.append("\n")
            size += 1
        if is_hashtag:
            hashtag_spans.append((size, size + len(shown)))
        display.append(shown)
        payload.append(sent)
        size += len(shown)
        after_link = is_link
    shown_text = "".join(display)
    for start, end in hashtag_spans:
        if (start and shown_text[start - 1] not in _SPACE) or _TAIL_RE.match(
            shown_text, end
        ) is None:
            raise LittleTextError(
                f"hashtag {shown_text[start:end]!r} must stand alone: put it at the start or after "
                "whitespace, and follow it with whitespace, closing punctuation or the end"
            )
    return shown_text, "".join(payload)


def _fold(tag: str) -> str:
    """Matching key for hashtags: Unicode canonical caseless form."""
    return unicodedata.normalize("NFC", unicodedata.normalize("NFD", tag).casefold())


def _approved(tags: Iterable[str]) -> dict[str, str]:
    """Map each approved tag's matching key to its canonical spelling."""
    approved: dict[str, str] = {}
    for entry in tags:
        tag = Hashtag(unicodedata.normalize("NFC", entry.removeprefix("#"))).tag
        if approved.setdefault(_fold(tag), tag) != tag:
            raise LittleTextError(
                f"approved hashtags {approved[_fold(tag)]!r} and {tag!r} differ only in case"
            )
    return approved
