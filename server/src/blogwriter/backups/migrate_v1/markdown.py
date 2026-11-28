"""v1 Markdown into the article dialect: frontmatter, MDX leftovers, headings and images.

v1 stored MDX-flavoured Markdown with a loose ``key: value`` frontmatter, injected a hero image
as the first line (``![title](/api/images/<id>)``), kept generated images in Cosmos behind
``/api/images/<id>`` and sometimes left ``data:`` placeholders. Its HTML render added a
"frontmatter banner" (``<div class="frontmatter">``); v2 re-renders from the Markdown, so the
banner is gone by construction.

Everything here is pure and leaves fenced code blocks and inline code untouched.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

__all__ = [
    "Image",
    "clean_v1_markdown",
    "find_images",
    "referenced_v1_images",
    "rewrite_images",
    "split_v1_frontmatter",
    "take_leading_image",
    "v1_image_id",
    "v1_tags",
]

_V1_FRONTMATTER: Final = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_FENCE_OPEN: Final = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_INLINE_CODE: Final = re.compile(r"(?<!`)(`+)(?!`).+?(?<!`)\1(?!`)", re.DOTALL)
_MDX_STATEMENT: Final = re.compile(r"^(?:import|export)\s[^\n]*\n?", re.MULTILINE)
_JSX_TAG: Final = re.compile(r"</?[A-Z][A-Za-z0-9.]*(?:\s[^<>]*)?/?>")
_HTML_IMG: Final = re.compile(r"<img\b[^<>]*>", re.IGNORECASE)
_HTML_ATTR: Final = re.compile(r"""([a-zA-Z-]+)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+))""")
_ATX: Final = re.compile(r"^(?P<indent> {0,3})(?P<hashes>#{1,6})(?P<rest>(?:[ \t].*)?)$")
_CLOSING_HASHES: Final = re.compile(r"[ \t]+#+[ \t]*$")
_MD_IMAGE: Final = re.compile(
    r"!\[(?P<alt>(?:\\.|[^\[\]\\\n])*)\]"
    r"\([ \t]*(?P<src><[^<>\n]*>|[^\s()<>]+)"
    r"(?P<title>[ \t]+(?:\"[^\"\n]*\"|'[^'\n]*'))?[ \t]*\)"
)
_LEADING_SPACE_IMAGE: Final = re.compile(r"(?P<lead>[ \t]?)" + _MD_IMAGE.pattern)
_EMPTY_LINK: Final = re.compile(r"[ \t]?\[\]\([^()\n]*\)")
_BLANK_RUNS: Final = re.compile(r"\n{3,}")
_V1_IMAGE: Final = re.compile(
    r"^(?:https?://[^/\s]+)?/api/images/(?P<id>[A-Za-z0-9][A-Za-z0-9._-]{0,127})/?$"
)
_KEY_CHARS: Final = re.compile(r"[^0-9a-z]+")
_QUOTED: Final = re.compile(r'"([^"]+)"')


@dataclass(frozen=True, slots=True)
class Image:
    """A Markdown image as written: its (unescaped) source and alt text."""

    src: str
    alt: str


def split_v1_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """v1's own reading of its frontmatter: ``key: value`` lines, quotes stripped."""
    source = text.replace("\r\n", "\n").replace("\r", "\n")
    match = _V1_FRONTMATTER.match(source)
    if match is None:
        return {}, source
    meta: dict[str, str] = {}
    for line in match.group(1).strip().split("\n"):
        key, separator, value = line.partition(":")
        if separator and key.strip():
            meta[key.strip()] = value.strip().strip('"').strip("'")
    return meta, source[match.end() :]


def v1_tags(raw: str) -> list[str]:
    """Tags as v1 read them from frontmatter: every double-quoted string."""
    return _QUOTED.findall(raw)


def _segments(text: str) -> list[tuple[bool, str]]:
    """``(is_code, text)`` pieces split at fenced code blocks (an unclosed fence runs to EOF)."""
    out: list[tuple[bool, str]] = []
    prose: list[str] = []
    code: list[str] = []
    fence = ""
    for line in text.splitlines(keepends=True):
        if not fence:
            match = _FENCE_OPEN.match(line)
            if match is None:
                prose.append(line)
                continue
            if prose:
                out.append((False, "".join(prose)))
                prose = []
            fence = match.group(1)
            code = [line]
            continue
        code.append(line)
        stripped = line.strip()
        if stripped and set(stripped) == {fence[0]} and len(stripped) >= len(fence):
            out.append((True, "".join(code)))
            code, fence = [], ""
    if fence:
        out.append((True, "".join(code)))
    if prose:
        out.append((False, "".join(prose)))
    return out


def _outside_fences(text: str, transform: Callable[[str], str]) -> str:
    """Apply ``transform`` to whole prose lines; fenced code blocks are kept as they are."""
    return "".join(
        segment if is_code else transform(segment) for is_code, segment in _segments(text)
    )


def _outside_code(text: str, transform: Callable[[str], str]) -> str:
    """Apply ``transform`` to the prose of ``text``: fenced blocks and inline code are kept."""
    pieces: list[str] = []
    for is_code, segment in _segments(text):
        if is_code:
            pieces.append(segment)
            continue
        position = 0
        for match in _INLINE_CODE.finditer(segment):
            pieces.append(transform(segment[position : match.start()]))
            pieces.append(match.group(0))
            position = match.end()
        pieces.append(transform(segment[position:]))
    return "".join(pieces)


def _escape_alt(text: str) -> str:
    return text.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")


def _img_tag_to_markdown(match: re.Match[str]) -> str:
    attributes: dict[str, str] = {}
    for attribute in _HTML_ATTR.finditer(match.group(0)):
        value = next((group for group in attribute.groups()[1:] if group is not None), "")
        attributes.setdefault(attribute.group(1).lower(), value)
    src = attributes.get("src", "").strip()
    if not src or any(char in src for char in " ()<>"):
        return ""
    return f"![{_escape_alt(attributes.get('alt', '').strip())}]({src})"


def _key(text: str) -> str:
    return _KEY_CHARS.sub(" ", text.casefold()).strip()


class _Headings:
    """ATX headings into dialect levels: h1 -> h2 (or dropped when it repeats the title before
    any other heading), h5/h6 -> h4, empty headings dropped."""

    def __init__(self, title: str | None) -> None:
        self._title = _key(title) if title else None
        self._seen = False

    def __call__(self, prose: str) -> str:
        lines: list[str] = []
        for line in prose.split("\n"):
            match = _ATX.match(line)
            if match is None:
                lines.append(line)
                continue
            level = len(match["hashes"])
            rest = _CLOSING_HASHES.sub("", match["rest"])
            text = rest.strip()
            if text in ("", "#"):
                continue
            if level == 1:
                if not self._seen and self._title is not None and _key(text) == self._title:
                    self._seen = True
                    continue
                level = 2
            elif level > 4:
                level = 4
            self._seen = True
            lines.append(f"{'#' * level} {rest.lstrip()}")
        return "\n".join(lines)


def clean_v1_markdown(body: str, *, title: str | None) -> str:
    """The v1 body in dialect form, outside code: MDX imports and JSX tags removed (their text
    kept), ``<img>`` tags as Markdown images, headings at dialect levels, blank runs collapsed."""
    headings = _Headings(title)

    def lines(text: str) -> str:
        return headings(_MDX_STATEMENT.sub("", text))

    def inline(text: str) -> str:
        return _JSX_TAG.sub("", _HTML_IMG.sub(_img_tag_to_markdown, text))

    source = body.replace("\r\n", "\n").replace("\r", "\n")
    cleaned = _outside_code(_outside_fences(source, lines), inline)
    return _BLANK_RUNS.sub("\n\n", cleaned).strip("\n") + "\n"


def _unescape(text: str) -> str:
    return re.sub(r"\\(.)", r"\1", text)


def find_images(body: str) -> list[Image]:
    """Every Markdown image outside code, in order."""
    found: list[Image] = []

    def collect(text: str) -> str:
        for match in _MD_IMAGE.finditer(text):
            found.append(Image(src=match["src"].strip("<>"), alt=_unescape(match["alt"])))
        return text

    _outside_code(body, collect)
    return found


def rewrite_images(body: str, resolve: Callable[[Image], str | None]) -> str:
    """Replace each image's source with ``resolve(image)``; ``None`` drops the image."""

    def replace(match: re.Match[str]) -> str:
        image = Image(src=match["src"].strip("<>"), alt=_unescape(match["alt"]))
        target = resolve(image)
        if target is None:
            return ""  # with the space before it: "see ![x](data:...)." -> "see."
        if target == image.src:
            return match.group(0)
        return f"{match['lead']}![{match['alt']}]({target}{match['title'] or ''})"

    def prose(text: str) -> str:
        return _EMPTY_LINK.sub("", _LEADING_SPACE_IMAGE.sub(replace, text))

    rewritten = _outside_code(body, prose)
    return _BLANK_RUNS.sub("\n\n", rewritten).strip("\n") + "\n"


def take_leading_image(body: str) -> tuple[Image | None, str]:
    """The image on the body's first non-blank line when it stands alone (v1's injected hero),
    and the body without it."""
    stripped = body.lstrip("\n")
    first, _, rest = stripped.partition("\n")
    match = _MD_IMAGE.fullmatch(first.strip())
    if match is None:
        return None, body
    image = Image(src=match["src"].strip("<>"), alt=_unescape(match["alt"]))
    return image, rest.lstrip("\n")


def v1_image_id(src: str) -> str | None:
    """The v1 image id of ``/api/images/<id>`` (relative or on any host), else ``None``."""
    match = _V1_IMAGE.fullmatch(src.strip())
    return match["id"] if match is not None else None


def referenced_v1_images(markdown: str) -> list[str]:
    """Ids of every v1 image a v1 document's Markdown shows (sorted, unique)."""
    meta, body = split_v1_frontmatter(markdown)
    cleaned = clean_v1_markdown(body, title=meta.get("title"))
    ids = {v1_image_id(image.src) for image in find_images(cleaned)}
    return sorted(image_id for image_id in ids if image_id is not None)
