"""python_regex: contract patterns mean the same in Python as in ECMA-262 (u flag)."""

from __future__ import annotations

import re

import pytest

from blogwriter.publish.contract import python_regex


def _search(pattern: str, value: str) -> bool:
    return re.search(python_regex(pattern), value) is not None


def test_dollar_matches_only_at_the_very_end() -> None:
    assert re.search("^abc$", "abc\n")  # the Python behaviour being corrected
    assert not _search("^abc$", "abc\n")
    assert _search("^abc$", "abc")
    assert not _search(r"\.png$", "x.png\n")


def test_escaped_and_class_dollars_stay_literal() -> None:
    assert _search(r"^a\$$", "a$")
    assert _search("^[$]$", "$")
    assert python_regex(r"^a\$$") == r"^a\$\Z"


@pytest.mark.parametrize(
    ("char", "is_space"),
    [
        (" ", True),
        ("\t", True),
        ("\n", True),
        ("\x0b", True),
        ("\x0c", True),
        ("\r", True),
        ("\xa0", True),
        ("\u1680", True),
        ("\u2000", True),
        ("\u200a", True),
        ("\u2028", True),
        ("\u2029", True),
        ("\u202f", True),
        ("\u205f", True),
        ("\u3000", True),
        ("\ufeff", True),  # ECMA-262 yes, Python's \s no
        ("\x1c", False),  # Python's \s yes, ECMA-262 no
        ("\x1f", False),
        ("\x85", False),
        ("\u200b", False),
        ("\u180e", False),
        ("a", False),
    ],
)
def test_whitespace_follows_ecma(char: str, is_space: bool) -> None:
    assert _search(r"^\s$", char) is is_space
    assert _search(r"^\S$", char) is not is_space
    assert _search(r"^[^\sx]$", char) is not is_space


@pytest.mark.parametrize("char", ["\n", "\r", "\u2028", "\u2029"])
def test_dot_excludes_ecma_line_terminators(char: str) -> None:
    assert not _search("^.$", char)
    assert _search("^.$", "\x85")


def test_dot_inside_a_class_is_literal() -> None:
    assert _search("^[.]$", ".")
    assert not _search("^[.]$", "a")


@pytest.mark.parametrize(
    "pattern",
    [r"\d", r"\D", r"\w", r"\W", r"\b", r"\B", r"\p{L}", r"\P{L}", r"\k<x>", r"[\d]"],
)
def test_engine_dependent_escapes_are_refused(pattern: str) -> None:
    with pytest.raises(ValueError, match="engine-dependent"):
        python_regex(pattern)


@pytest.mark.parametrize(
    ("pattern", "message"),
    [
        ("abc\\", "lone backslash"),
        ("[abc", "unterminated"),
        ("[]", "empty character classes"),
        ("[^]", "empty character classes"),
        ("[a[b]", "must be escaped"),
        (r"[\S]", "inside a character class"),
    ],
)
def test_ambiguous_syntax_is_refused(pattern: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        python_regex(pattern)


def test_other_escapes_pass_through() -> None:
    pattern = r"^[^\\\x00-\x1f\x7f\u2028]\.(?![/\\])$"
    assert python_regex(pattern) == r"^[^\\\x00-\x1f\x7f\u2028]\.(?![/\\])\Z"
    assert _search(pattern, "a.")
    assert not _search(pattern, "\x01.")
