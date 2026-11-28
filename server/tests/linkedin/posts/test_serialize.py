"""The serializer invariants: the API payload is exactly the escaped display text, the kit text
is the display text, and serializing is idempotent (contract W-B3 invariant tests)."""

from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from blogwriter.linkedin.littletext import (
    RESERVED_CHARACTERS,
    Link,
    LittleTextError,
    Text,
    api_commentary,
    display_text,
    parse_display,
    unescape,
)
from blogwriter.linkedin.posts.serialize import clean_text, payload_units, serialize

APPROVED = ("AISecurity", "AgentIdentity")
GOLDENS = Path(__file__).parent / "goldens"

SAMPLES = (
    "We moved 2,000 apps in 14 months.\n\nThe last 600 set the date.",
    "Scopes: read_write vs read-only (and why *both* matter) <here>",
    "Tags at the end.\n\n#AISecurity #AgentIdentity",
    "#aisecurity in lower case takes the approved spelling",
    "C# and F# are languages, foo#bar is not a tag, #NotApproved stays text",
    "Back\\slash, pipe | braces {} brackets [] tilde ~ at @ underscore _",
    "Line one\r\nLine two\rLine three",
    "Emoji and accents: café, naïve, 🔐 once",
    "A mention-looking @[Name](urn:li:person:abc) stays text",
    "Closing punctuation after a tag works: #AISecurity.",
)


def _invariants(display: str, payload: str) -> None:
    assert payload == api_commentary(parse_display(display, APPROVED))
    assert unescape(payload) == display
    assert display_text(parse_display(display, APPROVED)) == display


@pytest.mark.parametrize("text", SAMPLES)
def test_payload_is_the_escaped_display_text(text: str) -> None:
    display, payload = serialize(text, APPROVED)
    _invariants(display, payload)
    assert serialize(display, APPROVED) == (display, payload)


def test_golden_pairs() -> None:
    golden = json.loads((GOLDENS / "serialize.json").read_text(encoding="utf-8"))
    produced = [
        {"input": text, "display": pair[0], "payload": pair[1]}
        for text in SAMPLES
        for pair in [serialize(text, APPROVED)]
    ]
    assert produced == golden


def test_only_approved_hashtags_go_out_unescaped() -> None:
    display, payload = serialize("#AISecurity and #Other and C# here", APPROVED)
    assert "#AISecurity" in payload
    assert "\\#Other" in payload
    assert "C\\#" in payload
    assert display == "#AISecurity and #Other and C# here"


def test_hashtags_take_the_approved_spelling() -> None:
    display, payload = serialize("Ends with #agentidentity", APPROVED)
    assert display == "Ends with #AgentIdentity"
    assert payload.endswith("#AgentIdentity")


def test_every_reserved_character_is_escaped_in_text() -> None:
    text = "".join(sorted(RESERVED_CHARACTERS))
    display, payload = serialize(text, ())
    assert display == text
    assert payload == "".join("\\" + char for char in sorted(RESERVED_CHARACTERS))


def test_an_excepted_link_on_its_own_line_matches_the_link_token_layout() -> None:
    url = "https://example.org/a_b(c)"
    display, payload = serialize(f"Intro\n{url}\nOutro", ())
    tokens = [Text("Intro\n"), Link(url), Text("\nOutro")]
    assert payload == api_commentary(tokens, allow_links=True)
    assert display == display_text(tokens)


def test_control_characters_are_refused_and_cleaned() -> None:
    with pytest.raises(LittleTextError):
        serialize("bell\x07here", ())
    assert clean_text("a\x07b\r\nc  \n\n\n\nd ") == "ab\nc\n\nd"


def test_payload_units_count_utf16_after_escaping() -> None:
    assert payload_units("ab") == 2
    assert payload_units("\\#") == 2
    assert payload_units("🔐") == 2


def test_fuzzed_texts_keep_the_invariants() -> None:
    rng = random.Random(20261126)
    alphabet = [
        *sorted(RESERVED_CHARACTERS),
        "a",
        "Z",
        "1",
        " ",
        "\n",
        "é",
        "🔐",
        "\u2019",
        "#AISecurity",
        "#agentidentity",
        "#Nope",
        ".",
        ",",
        ")",
    ]
    for _ in range(500):
        text = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 60)))
        display, payload = serialize(text, APPROVED)
        _invariants(display, payload)
        assert serialize(display, APPROVED) == (display, payload)
