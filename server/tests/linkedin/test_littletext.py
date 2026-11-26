"""Golden and property tests for the LinkedIn little-text serializer.

Plan: "LinkedIn Studio" > Serializer, and the golden tests under "Verification" (every reserved
character, hashtags, C#, URLs).
"""

from __future__ import annotations

import dataclasses
import itertools
import random
import string
import unicodedata

import pytest

from blogwriter.linkedin.littletext import (
    RESERVED_CHARACTERS,
    Hashtag,
    Link,
    LinkNotAllowedError,
    LittleTextError,
    Mention,
    Text,
    Token,
    api_commentary,
    display_text,
    escape,
    parse_display,
    unescape,
)

RESERVED = "|{}@[]()<>#\\*_~"
APPROVED = ("AISecurity", "IdentityAndAccess", "AzureAI")
PERSON = "urn:li:person:Ab3_dE-fG9"
ORG = "urn:li:organization:2414183"
URL = "https://example.com/post"

ACUTE = "\N{COMBINING ACUTE ACCENT}"
ZWJ = "\N{ZERO WIDTH JOINER}"
ROCKET = "\N{ROCKET}"
NBSP = "\N{NO-BREAK SPACE}"
RSQUO = "\N{RIGHT SINGLE QUOTATION MARK}"
RDQUO = "\N{RIGHT DOUBLE QUOTATION MARK}"
CAFE = "Caf\N{LATIN SMALL LETTER E WITH ACUTE}"
HINDI = (
    "\N{DEVANAGARI LETTER HA}\N{DEVANAGARI VOWEL SIGN I}\N{DEVANAGARI SIGN ANUSVARA}"
    "\N{DEVANAGARI LETTER DA}\N{DEVANAGARI VOWEL SIGN II}"
)
NIHON = "\N{CJK UNIFIED IDEOGRAPH-65E5}\N{CJK UNIFIED IDEOGRAPH-672C}"


def bare_reserved(payload: str) -> list[str]:
    """Reserved characters a payload sends unescaped; fails on a backslash that escapes nothing."""
    bare: list[str] = []
    i = 0
    while i < len(payload):
        if payload[i] == "\\":
            assert payload[i + 1 : i + 2] in RESERVED_CHARACTERS, f"stray backslash at {i}"
            i += 2
            continue
        if payload[i] in RESERVED_CHARACTERS:
            bare.append(payload[i])
        i += 1
    return bare


# --- Reserved characters -------------------------------------------------------------------------


def test_reserved_set_is_the_documented_fifteen() -> None:
    assert len(RESERVED) == 15
    assert frozenset(RESERVED) == RESERVED_CHARACTERS


@pytest.mark.parametrize("ch", list(RESERVED), ids=[unicodedata.name(c) for c in RESERVED])
def test_each_reserved_character_alone(ch: str) -> None:
    tokens = [Text(ch)]
    assert api_commentary(tokens) == "\\" + ch
    assert display_text(tokens) == ch
    assert unescape(api_commentary(tokens)) == ch


@pytest.mark.parametrize("ch", list(RESERVED), ids=[unicodedata.name(c) for c in RESERVED])
def test_each_reserved_character_inside_words(ch: str) -> None:
    assert api_commentary([Text(f"a{ch}b {ch}c d{ch}")]) == f"a\\{ch}b \\{ch}c d\\{ch}"


def test_all_reserved_characters_combined() -> None:
    combined = RESERVED + RESERVED[::-1] + " x " + RESERVED + RESERVED
    sent = "".join("\\" + c if c in RESERVED else c for c in combined)
    assert api_commentary([Text(combined)]) == sent
    assert bare_reserved(sent) == []
    assert unescape(sent) == combined


def test_realistic_sentence_golden() -> None:
    text = "Call f(x) on [beta] <T> with a_b, *bold* ~x~ {k|v} @team #1 C:\\tmp"
    assert api_commentary([Text(text)]) == (
        r"Call f\(x\) on \[beta\] \<T\> with a\_b, \*bold\* \~x\~ \{k\|v\} \@team \#1 C:\\tmp"
    )


def test_unreserved_characters_pass_through() -> None:
    others = "".join(c for c in string.printable if c not in RESERVED)
    assert escape(others) == others
    lookalikes = (
        "\N{FULLWIDTH NUMBER SIGN}\N{FULLWIDTH COMMERCIAL AT}\N{FULLWIDTH LEFT PARENTHESIS}"
        "\N{FULLWIDTH RIGHT PARENTHESIS}\N{FULLWIDTH LOW LINE}\N{FULLWIDTH TILDE}"
    )
    assert escape(lookalikes) == lookalikes


# --- Backslash: one pass, never double escaped ---------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "sent"),
    [
        ("\\", "\\\\"),
        ("\\\\", "\\\\\\\\"),
        ("\\#", "\\\\\\#"),
        ("#\\", "\\#\\\\"),
        ("\\(x\\)", "\\\\\\(x\\\\\\)"),
        ("a\\_b", "a\\\\\\_b"),
        ("\\n is not a newline", "\\\\n is not a newline"),
    ],
)
def test_backslash_is_escaped_in_the_same_single_pass(raw: str, sent: str) -> None:
    assert escape(raw) == sent
    assert api_commentary([Text(raw)]) == sent
    assert unescape(sent) == raw


def test_escaping_adds_exactly_one_backslash_per_reserved_character() -> None:
    raw = "\\#tag \\\\(x) ~_~ @\\"
    sent = escape(raw)
    assert len(sent) == len(raw) + sum(c in RESERVED for c in raw)
    assert bare_reserved(sent) == []
    assert unescape(sent) == raw


# --- Round trip: unescape(api_commentary(tokens)) == display_text(tokens) -------------------------

ROUND_TRIP: dict[str, list[Token]] = {
    "empty": [],
    "plain": [Text("Plain words only.")],
    "lines": [Text("Line one\n\nLine two\twith a tab\n")],
    "reserved": [Text(RESERVED * 2)],
    "backslashes": [Text("C:\\path\\to\\file \\# \\@ \\\\")],
    "emoji": [
        Text(
            f"Rocket {ROCKET}, family \N{MAN}{ZWJ}\N{WOMAN}{ZWJ}\N{GIRL}, flag "
            "\N{REGIONAL INDICATOR SYMBOL LETTER U}\N{REGIONAL INDICATOR SYMBOL LETTER S}, keycap "
            "#\N{VARIATION SELECTOR-16}\N{COMBINING ENCLOSING KEYCAP}"
        )
    ],
    "scripts": [
        Text(
            f"{NIHON}, \N{ARABIC LETTER MEEM}\N{ARABIC LETTER REH}\N{ARABIC LETTER HAH}"
            f"\N{ARABIC LETTER BEH}\N{ARABIC LETTER ALEF}, cafe{ACUTE}, {HINDI}"
        )
    ],
    "hashtags": [
        Text("Notes (draft) "),
        Hashtag("AISecurity"),
        Text(", "),
        Hashtag("AzureAI"),
        Text(".\n\n"),
        Hashtag("IdentityAndAccess"),
    ],
    "mentions": [
        Text("Thanks "),
        Mention("Jane Doe", PERSON),
        Text(" & "),
        Mention("Contoso", ORG),
        Text("!"),
    ],
    "markup_lookalikes": [Text("@[Jane](urn:li:person:abc) {hashtag|\\#|AI} <b>*x*</b>")],
    "links": [
        Text("Read (more):"),
        Link("https://example.com/a_b?utm_source=li&x=(1)#part"),
        Text("Bye"),
    ],
    "link_only": [Link(URL)],
    "everything": [
        Text("Ship (v2) "),
        Hashtag("AzureAI"),
        Text(" with "),
        Mention("Jane O'Neil", PERSON),
        Text(":"),
        Link(URL),
        Text("C# \\o/ ~fin~ "),
        Hashtag("AISecurity"),
    ],
}


@pytest.mark.parametrize("tokens", list(ROUND_TRIP.values()), ids=list(ROUND_TRIP))
def test_unescape_of_payload_is_the_display_text(tokens: list[Token]) -> None:
    assert unescape(api_commentary(tokens, allow_links=True)) == display_text(tokens)


@pytest.mark.parametrize(
    "name", ["plain", "lines", "reserved", "backslashes", "emoji", "scripts", "hashtags"]
)
def test_display_text_parses_back_to_the_same_tokens(name: str) -> None:
    tokens = ROUND_TRIP[name]
    assert parse_display(display_text(tokens), APPROVED) == tokens


FRAGMENTS = [
    *RESERVED,
    *("#", "#AI", "#ai", "#AISecurity", "#azureai", "#Other", "AI", "C", "x", "7"),
    *(" ", " ", " ", " ", "\n", "\t", "\r\n", "\r", ".", ",", "'", RSQUO, "-", "!", ")"),
    *(ACUTE, ZWJ, "\N{VARIATION SELECTOR-16}", ROCKET, "\N{FULLWIDTH NUMBER SIGN}", NBSP),
]


def test_fuzzed_text_round_trips() -> None:
    rng = random.Random(20261126)
    approved = (*APPROVED, "AI")
    hashtags_seen = 0
    for _ in range(400):
        typed = "".join(rng.choice(FRAGMENTS) for _ in range(rng.randint(0, 30)))
        typed_lf = typed.replace("\r\n", "\n").replace("\r", "\n")
        tokens = parse_display(typed, approved)
        shown = display_text(tokens)
        payload = api_commentary(tokens)

        assert unescape(payload) == shown
        assert unescape(escape(typed_lf)) == typed_lf
        assert shown.casefold() == typed_lf.casefold()  # only hashtag casing may change
        assert parse_display(shown, approved) == tokens
        hashtags = sum(isinstance(token, Hashtag) for token in tokens)
        assert bare_reserved(payload) == ["#"] * hashtags  # nothing LinkedIn could truncate on
        assert Text("") not in tokens
        assert not any(
            isinstance(a, Text) and isinstance(b, Text) for a, b in itertools.pairwise(tokens)
        )
        hashtags_seen += hashtags
    assert hashtags_seen >= 20  # the fragments do exercise approved hashtags (32 with this seed)


def test_payload_is_the_escaped_display_text_when_nothing_is_structured() -> None:
    shown = "Plain (text) with *marks*, C# and #unapproved"
    tokens = parse_display(shown, APPROVED)
    assert display_text(tokens) == shown
    assert api_commentary(tokens) == escape(shown)


# --- C# and other hashes that are not hashtags ---------------------------------------------------


def test_c_sharp_and_mid_word_hashes_stay_text() -> None:
    typed = "I write C# and F#, see foo#AI, #C#, and #dotnet."
    tokens = parse_display(typed, ["C", "AI", "DotNet"])
    assert tokens == [
        Text("I write C# and F#, see foo#AI, #C#, and "),
        Hashtag("DotNet"),
        Text("."),
    ]
    assert api_commentary(tokens) == r"I write C\# and F\#, see foo\#AI, \#C\#, and #DotNet."


def test_c_sharp_built_by_hand_is_refused() -> None:
    with pytest.raises(LittleTextError, match="must stand alone"):
        api_commentary([Text("C"), Hashtag("Sharp")])


# --- Approved vs unapproved hashtags, casing -----------------------------------------------------


def test_only_approved_hashtags_become_tokens_in_canonical_casing() -> None:
    tokens = parse_display("New post #aisecurity #AZUREAI #Random #IdentityAndAccess", APPROVED)
    assert tokens == [
        Text("New post "),
        Hashtag("AISecurity"),
        Text(" "),
        Hashtag("AzureAI"),
        Text(" #Random "),
        Hashtag("IdentityAndAccess"),
    ]
    assert display_text(tokens) == "New post #AISecurity #AzureAI #Random #IdentityAndAccess"
    assert api_commentary(tokens) == r"New post #AISecurity #AzureAI \#Random #IdentityAndAccess"


def test_with_no_approved_hashtags_every_hash_is_escaped() -> None:
    tokens = parse_display("#AISecurity #AzureAI", [])
    assert tokens == [Text("#AISecurity #AzureAI")]
    assert api_commentary(tokens) == r"\#AISecurity \#AzureAI"


@pytest.mark.parametrize(
    ("typed", "expected"),
    [
        ("#AI", [Hashtag("AI")]),
        ("x #AI", [Text("x "), Hashtag("AI")]),
        ("x\n#AI\n", [Text("x\n"), Hashtag("AI"), Text("\n")]),
        ("x\t#AI", [Text("x\t"), Hashtag("AI")]),
        ("a\r\n#AI", [Text("a\n"), Hashtag("AI")]),
        ("#AI.", [Hashtag("AI"), Text(".")]),
        ("#AI, #ai!", [Hashtag("AI"), Text(", "), Hashtag("AI"), Text("!")]),
        ("#AI). x", [Hashtag("AI"), Text("). x")]),
        (f"#AI{RSQUO} #AI{RDQUO}", [Hashtag("AI"), Text(f"{RSQUO} "), Hashtag("AI"), Text(RDQUO)]),
        (f"#AI {ROCKET}", [Hashtag("AI"), Text(f" {ROCKET}")]),
    ],
)
def test_hashtags_are_recognised_as_whole_words(typed: str, expected: list[Token]) -> None:
    assert parse_display(typed, ["AI"]) == expected


@pytest.mark.parametrize(
    "typed",
    [
        "foo#AI",  # mid-word
        "(#AI)",  # not after whitespace
        f"{NBSP}#AI",  # a no-break space is not a boundary
        "##AI",
        "#AI#",
        "#AISecurity",  # a longer word than the approved tag
        "#AI_ML",
        "#AI-powered",
        "#AI's",
        f"#AI{RSQUO}s",
        "#AI.io",  # domain-like
        f"#AI{ACUTE}",  # the mark joins the I's grapheme cluster
        f"#AI{ZWJ}",
        f"#AI{ROCKET}",
        "#\N{FULLWIDTH LATIN CAPITAL LETTER A}\N{FULLWIDTH LATIN CAPITAL LETTER I}",
        "\N{FULLWIDTH NUMBER SIGN}AI",  # not the ASCII number sign
    ],
)
def test_hashtag_lookalikes_stay_text(typed: str) -> None:
    assert parse_display(typed, ["AI"]) == [Text(typed)]


def test_unicode_hashtags_match_caselessly_and_by_canonical_equivalence() -> None:
    tokens = parse_display(f"#CAF\N{LATIN CAPITAL LETTER E WITH ACUTE} and #cafe{ACUTE}", [CAFE])
    assert tokens == [Hashtag(CAFE), Text(" and "), Hashtag(CAFE)]
    assert display_text(tokens) == f"#{CAFE} and #{CAFE}"


def test_approved_entries_accept_a_leading_hash_and_duplicates() -> None:
    assert parse_display("#azureai", ["#AzureAI", "AzureAI"]) == [Hashtag("AzureAI")]


def test_approved_entries_that_differ_only_in_case_are_rejected() -> None:
    with pytest.raises(LittleTextError, match="differ only in case"):
        parse_display("x", ["AzureAI", "AZUREAI"])


@pytest.mark.parametrize(
    "entry",
    [
        "",
        "#",
        "##AI",
        "AI Security",
        "AI_Security",
        "AI-Security",
        "2026",
        "C#",
        "AI.",
        "A\\I",
        "A@I",
        f"AI{ZWJ}",
        f"{ACUTE}AI",
        "AI\n",
    ],
)
def test_invalid_approved_hashtags_are_rejected(entry: str) -> None:
    with pytest.raises(LittleTextError, match="invalid hashtag"):
        parse_display("text", [entry])


def test_approved_hashtags_must_not_be_a_bare_string() -> None:
    with pytest.raises(TypeError):
        parse_display("#AI", "AI")


@pytest.mark.parametrize(
    "tag", ["", "#AI", "AI ML", "AI_ML", "AI-ML", "2026", f"Cafe{ACUTE}", f"{ACUTE}A", f"AI{ZWJ}"]
)
def test_invalid_hashtag_tokens_are_rejected(tag: str) -> None:
    with pytest.raises(LittleTextError):
        Hashtag(tag)


@pytest.mark.parametrize("tag", ["AI", "Web3", "AISecurity", NIHON, CAFE, HINDI])
def test_valid_hashtags_go_out_unescaped(tag: str) -> None:
    tokens = [Text("x "), Hashtag(tag)]
    assert api_commentary(tokens) == f"x #{tag}"
    assert parse_display(display_text(tokens), [tag]) == tokens


@pytest.mark.parametrize(
    "tokens",
    [
        pytest.param([Hashtag("AI"), Text("Security")], id="would-publish-AISecurity"),
        pytest.param([Hashtag("AI"), Hashtag("ML")], id="hashtag-hashtag"),
        pytest.param([Text("("), Hashtag("AI"), Text(")")], id="after-parenthesis"),
        pytest.param([Hashtag("AI"), Text("_x")], id="underscore"),
        pytest.param([Hashtag("AI"), Text(ACUTE)], id="combining-mark"),
        pytest.param([Hashtag("AI"), Text(".x")], id="domain-like"),
        pytest.param([Hashtag("AI"), Mention("Jane Doe", PERSON)], id="then-mention"),
        pytest.param([Mention("Jane Doe", PERSON), Hashtag("AI")], id="after-mention"),
        pytest.param([Text(f"x{NBSP}"), Hashtag("AI")], id="after-no-break-space"),
        pytest.param([Text("C"), Text(""), Hashtag("Sharp")], id="empty-text-between"),
    ],
)
def test_hashtags_that_would_run_into_neighbours_are_refused(tokens: list[Token]) -> None:
    with pytest.raises(LittleTextError, match="must stand alone"):
        api_commentary(tokens)
    with pytest.raises(LittleTextError, match="must stand alone"):
        display_text(tokens)


# --- Mentions ------------------------------------------------------------------------------------


def test_mention_formatting() -> None:
    tokens = [
        Text("Thanks "),
        Mention("Jane Doe", PERSON),
        Text(" and "),
        Mention("Contoso Ltd.", ORG),
        Text("!"),
    ]
    assert api_commentary(tokens) == f"Thanks @[Jane Doe]({PERSON}) and @[Contoso Ltd.]({ORG})!"
    assert display_text(tokens) == "Thanks @Jane Doe and @Contoso Ltd.!"


@pytest.mark.parametrize(
    "name",
    [
        "Jane O'Neil-Smith, PhD",
        "Zo\N{LATIN SMALL LETTER E WITH DIAERESIS} "
        "\N{LATIN CAPITAL LETTER A WITH RING ABOVE}ngstr\N{LATIN SMALL LETTER O WITH DIAERESIS}m",
        "\N{CJK UNIFIED IDEOGRAPH-674E}\N{CJK UNIFIED IDEOGRAPH-534E}",
        "J.R.R. Tolkien & Co!",
    ],
)
def test_mention_names_with_safe_punctuation_and_unicode(name: str) -> None:
    assert api_commentary([Mention(name, PERSON)]) == f"@[{name}]({PERSON})"


@pytest.mark.parametrize(
    "urn",
    [
        "",
        "urn:li:person:",
        "urn:li:member:123",
        "urn:li:company:123",
        "urn:li:organization:abc",
        "urn:li:organizationBrand:123",
        "URN:LI:PERSON:abc",
        " urn:li:person:abc",
        "urn:li:person:abc ",
        "urn:li:person:abc\n",
        "urn:li:person:a b",
        "urn:li:person:abc)",
        "urn:li:person:abc)(x",
        "urn:li:person:abc#x",
        "urn:li:organization:123/x",
    ],
)
def test_mention_urn_must_be_a_person_or_organization(urn: str) -> None:
    with pytest.raises(LittleTextError, match="URN"):
        Mention("Jane Doe", urn)


@pytest.mark.parametrize(
    "name",
    [
        "",
        " ",
        " Jane",
        "Jane ",
        "Jane\nDoe",
        "Jane\tDoe",
        "Jane\N{LINE SEPARATOR}Doe",
        "Jane\x00",
        *(f"Jane {ch} Doe" for ch in RESERVED),
    ],
)
def test_mention_names_that_need_escapes_or_span_lines_are_refused(name: str) -> None:
    with pytest.raises(LittleTextError, match="mention name"):
        Mention(name, PERSON)


def test_typed_markup_never_becomes_structure() -> None:
    typed = "@[Jane](urn:li:person:abc) {hashtag|\\#|AI} <b>*x*</b> a|b ~~y~~ [l](https://x.io)"
    tokens = parse_display(typed, ["AI"])
    assert tokens == [Text(typed)]
    payload = api_commentary(tokens)
    assert bare_reserved(payload) == []
    assert unescape(payload) == typed


# --- Links ---------------------------------------------------------------------------------------


def test_links_are_refused_unless_allowed() -> None:
    tokens = [Text("Read more: "), Link(URL)]
    with pytest.raises(LinkNotAllowedError):
        api_commentary(tokens)
    with pytest.raises(LinkNotAllowedError):
        api_commentary(tokens, allow_links=False)
    assert issubclass(LinkNotAllowedError, LittleTextError)
    assert api_commentary(tokens, allow_links=True) == f"Read more: \n{URL}"


def test_links_go_on_their_own_line() -> None:
    tokens = [Text("Read more:"), Link(URL), Text("Thanks")]
    assert api_commentary(tokens, allow_links=True) == f"Read more:\n{URL}\nThanks"
    assert display_text(tokens) == f"Read more:\n{URL}\nThanks"


def test_line_breaks_already_around_a_link_are_reused() -> None:
    tokens = [Text("Read:\n"), Link(URL), Text("\nBye")]
    assert api_commentary(tokens, allow_links=True) == f"Read:\n{URL}\nBye"


def test_links_at_the_edges_and_back_to_back() -> None:
    other = "https://example.org/x"
    assert display_text([Link(URL)]) == URL
    assert display_text([Link(URL), Link(other)]) == f"{URL}\n{other}"
    assert display_text([Link(URL), Text("after")]) == f"{URL}\nafter"
    assert display_text([Text("before"), Link(URL)]) == f"before\n{URL}"
    assert display_text([Text(""), Link(URL), Text("")]) == URL
    assert display_text([Link(URL), Hashtag("AI")]) == f"{URL}\n#AI"


def test_link_reserved_characters_are_escaped_like_text() -> None:
    url = "https://example.com/a_b(c)?utm_source=li&q=~x*#part"
    payload = api_commentary([Link(url)], allow_links=True)
    assert payload == r"https://example.com/a\_b\(c\)?utm\_source=li&q=\~x\*\#part"
    assert unescape(payload) == url


@pytest.mark.parametrize(
    "url",
    [
        "",
        "example.com",
        "//example.com/x",
        "ftp://example.com/x",
        "javascript:alert(1)",
        "HTTPS://EXAMPLE.COM/",
        "https://",
        "https:///path",
        "https://exa mple.com",
        "https://example.com/\n",
        "https://user@example.com/",
        "https://example.com/<script>",
        "https://example.com/%zz",
        "https://example.com:99999/",
        "https://[::1",
        "https://\N{CJK UNIFIED IDEOGRAPH-4F8B}.jp/",
    ],
)
def test_invalid_links_are_rejected(url: str) -> None:
    with pytest.raises(LittleTextError, match="invalid link"):
        Link(url)


# --- Text, unescape and token plumbing -----------------------------------------------------------


@pytest.mark.parametrize("bad", ["\x00", "a\rb", "\x1b[31m", "\x7f", "\x85", "\x0b", chr(0xD800)])
def test_text_rejects_control_characters_and_lone_surrogates(bad: str) -> None:
    with pytest.raises(LittleTextError, match="must not contain"):
        Text(bad)


def test_parse_display_normalizes_line_breaks_and_rejects_other_controls() -> None:
    assert parse_display("a\r\nb\rc", []) == [Text("a\nb\nc")]
    assert parse_display("", APPROVED) == []
    with pytest.raises(LittleTextError):
        parse_display("a\x00b", APPROVED)


def test_unescape_reads_linkedin_forms() -> None:
    payload = r"{hashtag|\#|AzureAI} by @[Jane \(JD\) Doe](urn:li:person:x1) \\o/"
    assert unescape(payload) == "#AzureAI by @Jane (JD) Doe \\o/"


@pytest.mark.parametrize(
    "payload",
    [
        "a(b",
        "trailing\\",
        "\\a",
        "@Jane",
        "@[Jane](urn:li:member:1)",
        "{hashtag|#|AI}",
        "x_y",
        "[l](https://x.io)",
    ],
)
def test_unescape_rejects_what_linkedin_would_truncate(payload: str) -> None:
    with pytest.raises(LittleTextError, match="malformed"):
        unescape(payload)


def test_any_iterable_of_tokens_works() -> None:
    assert api_commentary(t for t in [Text("a "), Hashtag("AI")]) == "a #AI"
    assert display_text(iter([Text("a "), Hashtag("AI")])) == "a #AI"


def test_non_tokens_are_a_type_error() -> None:
    with pytest.raises(TypeError):
        display_text(["raw string"])  # pyright: ignore[reportArgumentType]


def test_tokens_are_immutable_values() -> None:
    assert Text("a") == Text("a")
    assert len({Hashtag("AI"), Hashtag("AI"), Text("a")}) == 2
    with pytest.raises(dataclasses.FrozenInstanceError):
        Text("a").text = "b"  # pyright: ignore[reportAttributeAccessIssue]
