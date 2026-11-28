"""Deterministic entity detectors behind :func:`blogwriter.stories.intake.local_findings`.

Each detector returns code-point spans ``(start, end)`` into the text, in order. They are
regular expressions written to stay linear on long inputs (every repetition is delimited or
bounded), plus small checks in Python (``ipaddress`` validation, reserved documentation
domains and ranges, significant digits). No I/O, no state.
"""

from __future__ import annotations

import ipaddress
import string
from collections.abc import Collection, Iterator
from typing import Final

import regex

__all__ = [
    "PUBLIC_WORDS",
    "SENTENCE_STARTERS",
    "code_mask",
    "domain_spans",
    "email_spans",
    "figure_spans",
    "handle_spans",
    "ip_spans",
    "money_spans",
    "name_spans",
    "phone_spans",
    "url_spans",
]

type Span = tuple[int, int]

_TRAILING_PUNCTUATION: Final = ".,;:!?'\")]}>*_"

_URL: Final = regex.compile(r"(?i)(?<![\w@])(?:https?://|www\.)[^\s<>()\[\]{}\"'`]+")
_EMAIL: Final = regex.compile(r"(?<![\w.+\-])[\w.+\-]+@(?P<domain>[\w\-]+(?:\.[\w\-]+)+)")
_PHONES: Final = (
    # International: +<country> then 2-5 groups.
    regex.compile(r"(?<![\w+])\+\d{1,3}(?:[ .\-]?\(?\d{1,4}\)?){2,5}(?!\w)"),
    # North American: 555-123-4567, (555) 123-4567, 555.123.4567.
    regex.compile(r"(?<![\w.+\-])(?:\(\d{3}\)[ ]?|\d{3}[ .\-])\d{3}[ .\-]\d{4}(?!\w|[.\-]\d)"),
    # National numbers with a trunk prefix: 020 7946 0958, 0800-123-4567.
    regex.compile(r"(?<![\w.+\-])0\d{2,4}[ \-]\d{3,4}[ \-]\d{3,4}(?!\w|[.\-]\d)"),
)
_IPV4: Final = regex.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?:/\d{1,2})?(?!\w|\.\d)")
_IPV6: Final = regex.compile(
    r"(?i)(?<![\w:])(?:[0-9a-f]{0,4}:){2,7}[0-9a-f]{0,4}(?:/\d{1,3})?(?![\w:])"
)
_HANDLE: Final = regex.compile(r"(?<![\w@./+\-])@[A-Za-z_](?:[A-Za-z0-9_.\-]{0,48}[A-Za-z0-9_])?")
_DOMAIN_TLDS: Final = "com|org|net|io|ai|dev|app|co|cloud|gov|edu|us|uk|de|eu|ca|au|in|info|biz"
_DOMAIN: Final = regex.compile(
    r"(?i)(?<![\w@/.\-])(?:[a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?\.)+"
    rf"(?:{_DOMAIN_TLDS})(?![\w\-]|\.[a-z0-9])"
)
_MONEY: Final = regex.compile(
    r"(?i)(?<!\w)(?:"
    r"[$€£¥]\s?\d[\d,]*(?:\.\d+)?(?:\s?(?:k|m|mm|b|bn|thousand|million|billion)\b)?"
    r"|\d[\d,]*(?:\.\d+)?\s?(?:k|m|b|bn|thousand|million|billion)?\s?"
    r"(?:usd|eur|gbp|dollars|euros|pounds)\b)"
)
_NUMBER: Final = regex.compile(
    r"(?<![\w.,/:$€£¥#\-])(?P<int>\d{1,3}(?:,\d{3})+|\d+)(?:\.(?P<dec>\d+))?(?!\w|[.,]\d)"
)
_UNIT_AFTER: Final = regex.compile(
    r"(?i)\s?-?(?:bits?|bytes?|px|ms|sec|seconds?|kb|mb|gb|tb|pb|ghz|mhz|hz|dpi|ppi|nm|fps|rpm)\b"
)
_STANDARD_BEFORE: Final = regex.compile(
    r"(?i)\b(?:iso|iec|sp|rfc|cve|nist|fips|pci|soc|llm|asi|aml|tls|ssl|http|ipv|version|"
    r"top|ieee|ecma|cwe|capec)\s?[/\-]?\s*$"
)
_NAME_WORD: Final = r"\p{Lu}\p{Ll}+(?:\p{Lu}\p{Ll}+)*(?:['\u2019\-]\p{L}\p{Ll}*)?"
_NAME: Final = regex.compile(
    rf"(?<![\p{{L}}\p{{N}}_])(?:{_NAME_WORD})(?:[ ]+(?:{_NAME_WORD}))+(?![\p{{L}}\p{{N}}_])"
)
_WORD: Final = regex.compile(_NAME_WORD)
_FENCE_OPEN: Final = regex.compile(r"[ ]{0,3}(`{3,}|~{3,})")
_INLINE_CODE: Final = regex.compile(r"(`+)[^`\n](?:[^\n]*?[^`\n])?\1(?!`)")
_LEAD_IN: Final = " \t\"'\u201c\u2018(*-+>#"
_SENTENCE_BREAKS: Final = ".!?:;\n\r"

_RESERVED_DOMAINS: Final = frozenset({"example.com", "example.org", "example.net"})
_RESERVED_SUFFIXES: Final = (".example", ".test", ".invalid", ".localhost")
_IGNORED_V4: Final = frozenset(
    ipaddress.IPv4Address(address)
    for address in (
        "255.255.255.255",
        "169.254.169.254",  # the well-known cloud metadata endpoint
        "1.1.1.1",
        "1.0.0.1",
        "8.8.8.8",
        "8.8.4.4",
        "9.9.9.9",
    )
)
_DOC_NETS_V4: Final = tuple(
    ipaddress.IPv4Network(net) for net in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24")
)
_DOC_NET_V6: Final = ipaddress.IPv6Network("2001:db8::/32")


def _words(*chunks: str) -> frozenset[str]:
    return frozenset(word.casefold() for chunk in chunks for word in chunk.split())


SENTENCE_STARTERS: Final = _words(
    "A An The This That These Those It Its We I Our My Me You Your They Their He She His Her "
    "In On At As Of For From To By With Without Into Over Under After Before During While "
    "When Where Why What How Who Which Once Since Until If But And Or So Then Also Still Even "
    "Only Just Here There Now Today Yesterday Tomorrow Last Next First Second Third Every Each "
    "Some Most Many Few All Both No Not Yes One Two Three Four Five Six Seven Eight Nine Ten "
    "Dear Hi Hello Thanks Please"
)
"""Capitalised only because they start a sentence: never the first word of a name."""

PUBLIC_WORDS: Final = _words(
    # Months and days.
    "January February March April May June July August September October November December "
    "Monday Tuesday Wednesday Thursday Friday Saturday Sunday "
    # Vendors, products and standards bodies the pillars discuss (public by definition).
    "Microsoft Entra Azure Active Directory Okta Google Amazon Apple Linux Windows Office "
    "Exchange SharePoint Teams Intune Defender Sentinel Purview GitHub Copilot OpenAI "
    "Anthropic Claude ChatGPT Gemini Meta Llama Mistral Cisco Palo Alto Networks CrowdStrike "
    "Splunk ServiceNow Salesforce Oracle Workday Kubernetes Docker Terraform HashiCorp Vault "
    "Ping Identity ForgeRock Auth0 CyberArk SailPoint Saviynt Duo Yubico Cloudflare Zscaler "
    "Okta's Microsoft's Google's Amazon's Fortinet Wiz Snyk Datadog Elastic Atlassian Jira "
    "Confluence Slack Zoom Workspace Bedrock Foundry Vertex LinkedIn Twitter Mastodon Bluesky "
    "Owasp Mitre Atlas Nist Cisa Enisa Oecd Iso "
    # Common concepts that are capitalised in the field.
    "Zero Trust Conditional Access Single Sign Multi Factor Authentication Authorization "
    "Identity Governance Administration Privileged Management Security Operations Center "
    "Model Context Protocol Agent Agents Agentic Framework Risk Act European Union United "
    "States Kingdom Cloud Edge Enterprise Server Services Platform Studio Search Copilots "
    "Responsible Artificial Intelligence Machine Learning Large Language Models"
)
"""Words never treated as part of a private name (``possible-name`` is a warning anyway)."""


def _trim(text: str, start: int, end: int) -> Span:
    while end > start and text[end - 1] in _TRAILING_PUNCTUATION:
        end -= 1
    return start, end


def url_spans(text: str) -> Iterator[Span]:
    """``https://...``, ``http://...`` and ``www....`` links (trailing punctuation left out)."""
    for match in _URL.finditer(text):
        start, end = _trim(text, *match.span())
        if end - start > 4:
            yield start, end


def _reserved_domain(domain: str) -> bool:
    lowered = domain.lower().rstrip(".")
    return lowered in _RESERVED_DOMAINS or lowered.endswith(_RESERVED_SUFFIXES)


def email_spans(text: str) -> Iterator[Span]:
    """E-mail addresses, except the reserved documentation domains (``example.com``, ``.test``)."""
    for match in _EMAIL.finditer(text):
        if _reserved_domain(match.group("domain")):
            continue
        yield _trim(text, *match.span())


def phone_spans(text: str) -> Iterator[Span]:
    """International, North American and trunk-prefixed national numbers (8-15 digits)."""
    for pattern in _PHONES:
        for match in pattern.finditer(text):
            digits = sum(char.isdigit() for char in match.group(0))
            if 8 <= digits <= 15:
                yield match.span()


def _ignored_ip(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if address.is_unspecified or address.is_loopback:
        return True
    if isinstance(address, ipaddress.IPv4Address):
        return address in _IGNORED_V4 or any(address in net for net in _DOC_NETS_V4)
    return address in _DOC_NET_V6


def ip_spans(text: str) -> Iterator[Span]:
    """IPv4 and IPv6 addresses (with an optional prefix length), validated with ``ipaddress``.

    Documentation ranges, loopback, the unspecified address, the cloud metadata address and a
    few well-known public resolvers are not findings: they identify nobody.
    """
    for match in _IPV4.finditer(text):
        try:
            address = ipaddress.IPv4Address(match.group(0).split("/", 1)[0])
        except ValueError:
            continue
        if not _ignored_ip(address):
            yield match.span()
    for match in _IPV6.finditer(text):
        candidate = match.group(0).split("/", 1)[0]
        if sum(char in string.hexdigits for char in candidate) < 4:
            continue  # "a::b" or "::" read as code, not as an address
        try:
            address6 = ipaddress.IPv6Address(candidate)
        except ValueError:
            continue
        if not _ignored_ip(address6):
            yield match.span()


def handle_spans(text: str) -> Iterator[Span]:
    """Social and chat handles (``@name``); e-mail addresses are not handles."""
    for match in _HANDLE.finditer(text):
        yield _trim(text, *match.span())


def domain_spans(text: str) -> Iterator[Span]:
    """Bare domain names (``contoso-bank.com``) outside links and e-mail addresses."""
    for match in _DOMAIN.finditer(text):
        start, end = match.span()
        if _reserved_domain(match.group(0)):
            continue
        yield start, end


def money_spans(text: str) -> Iterator[Span]:
    """Money amounts (``$4.3M``, ``2 million dollars``, ``500 EUR``)."""
    for match in _MONEY.finditer(text):
        yield _trim(text, *match.span())


def _significant(int_part: str, dec_part: str) -> int:
    digits = (int_part.replace(",", "") + dec_part).lstrip("0").rstrip("0")
    return len(digits)


def figure_spans(text: str) -> Iterator[Span]:
    """Exact figures: numbers with three or more significant digits (``2,137``, ``43.7%``).

    Rounded numbers (``2,000``, ``40%``, ``1.5``), years (1900-2099), technical units
    (``256-bit``, ``120 ms``) and numbers inside standard ids (``SP 800-63``, ``ISO 42001``)
    are not findings.
    """
    for match in _NUMBER.finditer(text):
        int_part = match.group("int")
        dec_part = match.group("dec") or ""
        start, end = match.span()
        is_year = not dec_part and "," not in int_part and len(int_part) == 4
        if is_year and 1900 <= int(int_part) <= 2099:
            continue
        if _significant(int_part, dec_part) < 3:
            continue
        if _UNIT_AFTER.match(text, end):
            continue
        if _STANDARD_BEFORE.search(text[max(0, start - 12) : start]):
            continue
        yield start, end


def _sentence_start(text: str, start: int) -> bool:
    """Whether ``start`` begins a sentence, a line or a list item (lead-in characters skipped)."""
    index = start
    floor = max(0, start - 16)
    while index > floor and text[index - 1] in _LEAD_IN:
        index -= 1
    return index == 0 or text[index - 1] in _SENTENCE_BREAKS


def name_spans(text: str, public: Collection[str] = ()) -> Iterator[Span]:
    """Runs of two or more capitalised words that may name a person or an organisation.

    Words in :data:`PUBLIC_WORDS` or ``public`` (case-folded words) never count, and the first
    word of a run is dropped when it is a :data:`SENTENCE_STARTERS` word at the start of a
    sentence. A run that keeps at least two consecutive words is a span.
    """
    for match in _NAME.finditer(text):
        words = [(word.start(), word.end()) for word in _WORD.finditer(text, *match.span())]
        kept: list[Span] = []
        for index, (start, end) in enumerate(words):
            folded = text[start:end].casefold()
            base = folded[:-2] if folded.endswith(("'s", "\u2019s")) else folded
            if base in PUBLIC_WORDS or base in public:
                kept.append((-1, -1))
                continue
            if index == 0 and base in SENTENCE_STARTERS and _sentence_start(text, start):
                kept.append((-1, -1))
                continue
            kept.append((start, end))
        run: list[Span] = []
        for span in [*kept, (-1, -1)]:
            if span[0] >= 0:
                run.append(span)
                continue
            if len(run) >= 2:
                yield run[0][0], run[-1][1]
            run = []


def code_mask(text: str) -> bytearray:
    """One byte per code point: 1 inside Markdown fenced code blocks and inline code spans."""
    mask = bytearray(len(text))
    position = 0
    fence: str | None = None
    fence_start = 0
    for line in text.splitlines(keepends=True):
        if fence is None:
            opened = _FENCE_OPEN.match(line)
            if opened is not None:
                fence = opened.group(1)
                fence_start = position
        else:
            stripped = line.strip()
            if stripped.startswith(fence) and set(stripped) == {fence[0]}:
                mask[fence_start : position + len(line)] = b"\x01" * (
                    position + len(line) - fence_start
                )
                fence = None
        position += len(line)
    if fence is not None:
        mask[fence_start:] = b"\x01" * (len(text) - fence_start)
    for match in _INLINE_CODE.finditer(text):
        start, end = match.span()
        if not mask[start]:
            mask[start:end] = b"\x01" * (end - start)
    return mask
