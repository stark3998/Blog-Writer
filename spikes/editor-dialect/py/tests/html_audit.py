"""Independent check that HTML stays inside the sanitizer profile.

Parses the output with the standard library (not ammonia) and reports every
tag, attribute or value outside the profile, plus anything that looks
executable. Used on every snapshot and on the XSS corpus.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

from article_render.sanitizer import ALLOWED_ATTRIBUTES, ALLOWED_TAGS, attribute_filter

_EXECUTABLE_RE = re.compile(r"javascript:|vbscript:|data:|expression\(|url\(", re.IGNORECASE)


class _Auditor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.problems: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in ALLOWED_TAGS:
            self.problems.append(f"tag <{tag}>")
            return
        for name, value in attrs:
            text = value or ""
            allowed = ALLOWED_ATTRIBUTES.get(tag, frozenset()) | ({"rel"} if tag == "a" else set())
            if name not in allowed:
                self.problems.append(f"attribute {name} on <{tag}>")
            elif name.startswith("on"):
                self.problems.append(f"event handler {name} on <{tag}>")
            elif attribute_filter(tag, name, text) != text and name not in ("checked", "disabled"):
                self.problems.append(f"value {name}={text!r} on <{tag}>")
            if _EXECUTABLE_RE.search(text) and name not in ("title", "alt"):
                self.problems.append(f"executable value {name}={text!r} on <{tag}>")
            if name == "rel" and text != "noopener noreferrer":
                self.problems.append(f"rel={text!r}")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_comment(self, data: str) -> None:
        self.problems.append("comment")

    def handle_decl(self, decl: str) -> None:
        self.problems.append(f"declaration {decl!r}")

    def handle_pi(self, data: str) -> None:
        self.problems.append(f"processing instruction {data!r}")


def audit(html: str) -> list[str]:
    auditor = _Auditor()
    auditor.feed(html)
    auditor.close()
    return auditor.problems
